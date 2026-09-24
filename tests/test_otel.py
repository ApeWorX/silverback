"""Unit tests for Silverback OpenTelemetry integration (no live collector / no eth-ape)."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

# Prefer local stubs when eth-ape is not installed
_STUBS = Path(__file__).resolve().parents[1] / ".stubs"
if str(_STUBS) not in sys.path:
    sys.path.insert(0, str(_STUBS))

# Put package root on path for editable-less runs
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

pytest.importorskip("opentelemetry.sdk")

from silverback.types import ScalarDatapoint  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_otel(monkeypatch):
    import silverback.otel as otel

    otel.reset_for_tests()
    monkeypatch.delenv("SILVERBACK_ENABLE_OTEL", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    yield
    otel.reset_for_tests()


def test_soft_import_helpers_without_crash():
    from silverback.otel import (
        is_otel_env_configured,
        otel_packages_available,
        should_enable_otel,
    )

    assert otel_packages_available() is True
    assert is_otel_env_configured() is False
    assert should_enable_otel(False) is False
    assert should_enable_otel(True) is True


def test_should_enable_from_env(monkeypatch):
    from silverback.otel import should_enable_otel

    monkeypatch.setenv("SILVERBACK_ENABLE_OTEL", "true")
    assert should_enable_otel() is True

    monkeypatch.delenv("SILVERBACK_ENABLE_OTEL")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    assert should_enable_otel() is True

    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    assert should_enable_otel() is False


def test_configure_inmemory_and_record_datapoints():
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    from silverback.otel import (
        METRIC_HISTOGRAM_NAME,
        configure,
        get_bridge,
        record_task_metrics,
        reset_for_tests,
    )

    reset_for_tests()
    reader = InMemoryMetricReader()
    assert configure(force=True, meter_reader=reader) is True
    assert get_bridge() is not None

    metrics = {
        "gas_used": ScalarDatapoint(data=21_000),
        "price": ScalarDatapoint(data=Decimal("1.5")),
    }
    record_task_metrics(
        "on_swap",
        metrics,
        bot_name="testbot",
        ecosystem="ethereum",
        network="local",
        notify=False,
    )

    data = reader.get_metrics_data()
    assert data is not None
    found_histogram = False
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name == METRIC_HISTOGRAM_NAME:
                    found_histogram = True
                    points = list(metric.data.data_points)
                    assert len(points) >= 1
    assert found_histogram, "expected silverback.metric histogram export"


def test_metric_bridge_notifies_handlers():
    from silverback.otel import MetricBridge, configure, get_bridge, reset_for_tests

    reset_for_tests()
    assert configure(force=True, use_inmemory_exporters=True) is True
    bridge = get_bridge()
    assert isinstance(bridge, MetricBridge)

    seen: list[tuple] = []

    async def handler(datapoint, updated):
        seen.append((datapoint.data, updated))

    bridge.add_handler("gas_used", handler)
    completed = datetime.now(timezone.utc)
    coros = bridge.record(
        "gas_used",
        ScalarDatapoint(data=42),
        completed=completed,
        notify=True,
    )
    assert len(coros) == 1
    asyncio.run(coros[0])
    assert seen == [(42, completed)]


def test_handler_middleware_creates_spans():
    from taskiq import TaskiqMessage, TaskiqResult

    from silverback.otel import (
        HANDLER_SPAN_NAME,
        configure,
        create_handler_middleware,
        reset_for_tests,
    )

    reset_for_tests()
    assert configure(force=True, use_inmemory_exporters=True) is True
    mw = create_handler_middleware()
    assert mw is not None

    from silverback.otel import get_test_span_exporter

    exporter = get_test_span_exporter()
    assert exporter is not None

    msg = TaskiqMessage(
        task_id="t1",
        task_name="my_handler",
        labels={"task_type": "user:new-block", "block": "0xabc"},
        args=[],
        kwargs={},
    )
    mw.pre_execute(msg)
    result = TaskiqResult(is_err=False, return_value=None, execution_time=0.01, labels={})
    mw.post_execute(msg, result)

    spans = exporter.get_finished_spans()
    handler_spans = [s for s in spans if s.name == HANDLER_SPAN_NAME]
    assert len(handler_spans) == 1
    assert handler_spans[0].attributes["silverback.task_name"] == "my_handler"
    assert handler_spans[0].attributes["silverback.task_type"] == "user:new-block"


def test_metric_triggers_fire_via_bridge_only():
    """Metric triggers notify via MetricBridge; no separate result-path dual fire."""
    from silverback.otel import configure, get_bridge, record_task_metrics, reset_for_tests

    reset_for_tests()
    configure(force=True, use_inmemory_exporters=True)
    bridge = get_bridge()
    fired = []

    async def check_value(datapoint, updated):
        fired.append(datapoint.data)

    bridge.add_handler("tvl", check_value)

    # notify=True is the bridge trigger path (runner always passes this now)
    coros = record_task_metrics(
        "report",
        {"tvl": ScalarDatapoint(data=100.0)},
        bot_name="bot",
        completed=datetime.now(timezone.utc),
        notify=True,
    )
    for c in coros:
        asyncio.run(c)
    assert fired == [100.0]

    # notify=False records instruments but does not fire handlers (no dual path)
    fired.clear()
    coros = record_task_metrics(
        "report",
        {"tvl": ScalarDatapoint(data=200.0)},
        bot_name="bot",
        completed=datetime.now(timezone.utc),
        notify=False,
    )
    assert coros == []
    assert fired == []


def test_on_metric_raises_when_otel_unavailable(monkeypatch):
    from silverback.exceptions import OpenTelemetryRequired
    from silverback.otel import ensure_metric_bridge, reset_for_tests

    reset_for_tests()
    monkeypatch.setattr("silverback.otel.otel_packages_available", lambda: False)

    with pytest.raises(OpenTelemetryRequired, match="required for metric-value triggers"):
        ensure_metric_bridge()


def test_ensure_metric_bridge_works_without_otlp_env():
    """Local InMemoryBroker triggers need bridge without OTLP endpoint."""
    from silverback.otel import ensure_metric_bridge, get_bridge, reset_for_tests

    reset_for_tests()
    bridge = ensure_metric_bridge()
    assert bridge is get_bridge()
    assert bridge is not None


def test_instrument_broker_attaches_middleware():
    from taskiq import InMemoryBroker

    from silverback.otel import configure, instrument_broker, reset_for_tests

    reset_for_tests()
    configure(force=True, use_inmemory_exporters=True)
    broker = InMemoryBroker()
    assert instrument_broker(broker) is True
    # OpenTelemetryMiddleware should be present
    names = [type(m).__name__ for m in broker.middlewares]
    assert "OpenTelemetryMiddleware" in names
