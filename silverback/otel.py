"""
OpenTelemetry integration for Silverback (traces + user Datapoint metrics).

OpenTelemetry packages are a **hard dependency** (required for ``@bot.on_metric``
triggers via :class:`MetricBridge`). The MetricBridge and TaskIQ instrumentation
are configured through normal imports; standard ``OTEL_*`` environment variables
control OTLP exporter configuration. The bridge works in-process without an OTLP
endpoint.

Process-wide exporter / resource configuration is expected to move to Ape
(ape-config + OTEL_* overrides). This module bootstraps providers when none
exist yet, and owns Silverback-specific instrumentation (TaskIQ, handler spans,
Datapoint → metric bridge, metric-trigger notifier).
"""

from __future__ import annotations

import atexit
import os
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Callable, Coroutine

try:
    from ape.logging import get_logger
except ImportError:  # pragma: no cover — tests / minimal envs
    import logging

    def get_logger(name: str):  # type: ignore[misc]
        return logging.getLogger(name)

if TYPE_CHECKING:
    from taskiq import AsyncBroker, TaskiqMessage, TaskiqMiddleware, TaskiqResult

    from silverback.types import Datapoint

logger = get_logger(__name__)

# Instrument names (stable)
METRIC_HISTOGRAM_NAME = "silverback.metric"
METRIC_GAUGE_NAME = "silverback.metric.latest"
HANDLER_SPAN_NAME = "silverback.handler"

_configured = False
_bridge: "MetricBridge | None" = None
_meter_provider: Any = None
_tracer_provider: Any = None
_test_span_exporter: Any = None
_test_metric_reader: Any = None


class MetricBridge:
    """
    Dual-write Datapoints to OTel instruments and notify metric-value trigger handlers.

    Runner registers threshold callbacks via ``add_handler``. Metric-value triggers
    (``@bot.on_metric``) fire **only** through this bridge — not via the runner
    result-loop dual path.
    """

    def __init__(self, meter: Any | None = None):
        self._meter = meter
        self._latest: dict[str, float] = {}
        self._handlers: dict[str, list[Callable[..., Coroutine]]] = defaultdict(list)
        self._histogram = None
        self._gauge = None
        if meter is not None:
            self._histogram = meter.create_histogram(
                name=METRIC_HISTOGRAM_NAME,
                unit="1",
                description="Silverback user Datapoint observations",
            )
            self._gauge = meter.create_observable_gauge(
                name=METRIC_GAUGE_NAME,
                callbacks=[self._observe_latest],
                unit="1",
                description="Latest Silverback user Datapoint values",
            )

    def _observe_latest(self, options: Any):
        from opentelemetry.metrics import Observation

        for name, value in list(self._latest.items()):
            yield Observation(value, {"silverback.metric_name": name})

    def add_handler(self, metric_name: str, handler: Callable[..., Coroutine]) -> None:
        self._handlers[metric_name].append(handler)

    def clear_handlers(self) -> None:
        self._handlers.clear()

    def record(
        self,
        metric_name: str,
        datapoint: "Datapoint",
        attributes: dict[str, Any] | None = None,
        completed: datetime | None = None,
        *,
        notify: bool = True,
    ) -> list[Coroutine]:
        """
        Record a datapoint to OTel (if configured) and optionally build notify coroutines.

        Returns coroutines for handlers (caller schedules them on the runner task group).
        """
        from silverback.types import ScalarDatapoint

        attrs = dict(attributes or {})
        attrs.setdefault("silverback.metric_name", metric_name)

        value: float | None = None
        if isinstance(datapoint, ScalarDatapoint):
            data = datapoint.data
            if isinstance(data, bool):
                value = 1.0 if data else 0.0
            elif isinstance(data, Decimal):
                value = float(data)
            else:
                value = float(data)

        if value is not None:
            self._latest[metric_name] = value
            if self._histogram is not None:
                self._histogram.record(value, attrs)

        coros: list[Coroutine] = []
        if notify and completed is not None:
            for handler in self._handlers.get(metric_name, []):
                coros.append(handler(datapoint, completed))
        return coros


def get_bridge() -> MetricBridge | None:
    return _bridge


def ensure_metric_bridge(*, force: bool = False) -> MetricBridge:
    """
    Ensure MetricBridge is configured for ``@on_metric`` triggers.

    Raises:
        RuntimeError: if MetricBridge initialization did not produce a bridge.
    """
    if not configure(force=force) or get_bridge() is None:
        raise RuntimeError("MetricBridge failed to initialize; check OTel configuration.")

    bridge = get_bridge()
    assert bridge is not None
    return bridge


def _shutdown_providers() -> None:
    global _meter_provider, _tracer_provider
    for provider in (_tracer_provider, _meter_provider):
        if provider is None:
            continue
        try:
            provider.shutdown()
        except Exception:  # noqa: BLE001 — best-effort atexit
            pass


def configure(
    *,
    force: bool = False,
    use_inmemory_exporters: bool = False,
    meter_reader: Any | None = None,
) -> bool:
    """
    Bootstrap TracerProvider / MeterProvider if missing, and create MetricBridge.

    Returns True if OTel instrumentation is ready.

    ``use_inmemory_exporters`` / ``meter_reader`` are for unit tests. Production
    uses OTEL_* env (and later Ape-configured providers).
    """
    global _configured, _bridge, _meter_provider, _tracer_provider

    if _configured and not force:
        return _bridge is not None

    from opentelemetry import metrics, trace
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider

    service_name = os.environ.get("OTEL_SERVICE_NAME", "silverback")
    resource = Resource.create({"service.name": service_name})

    # Respect providers already set (future: Ape configures these).
    existing_tracer = trace.get_tracer_provider()
    existing_meter = metrics.get_meter_provider()

    need_tracer = use_inmemory_exporters or not _provider_is_real(existing_tracer)
    need_meter = (
        use_inmemory_exporters or meter_reader is not None or not _provider_is_real(existing_meter)
    )

    if need_tracer:
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
            SimpleSpanProcessor,
        )

        tracer_provider = TracerProvider(resource=resource)
        if use_inmemory_exporters:
            from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
                InMemorySpanExporter,
            )

            exporter = InMemorySpanExporter()
            tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
            global _test_span_exporter
            _test_span_exporter = exporter
            tracer_provider._silverback_span_exporter = exporter  # type: ignore[attr-defined]
        else:
            span_exporter = _build_otlp_span_exporter()
            if span_exporter is not None:
                tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
        trace.set_tracer_provider(tracer_provider)
        _tracer_provider = tracer_provider
    else:
        _tracer_provider = existing_tracer

    readers = []
    if meter_reader is not None:
        readers.append(meter_reader)
    elif use_inmemory_exporters:
        from opentelemetry.sdk.metrics.export import InMemoryMetricReader

        mem_reader = InMemoryMetricReader()
        readers.append(mem_reader)
    else:
        periodic = _build_otlp_metric_reader()
        if periodic is not None:
            readers.append(periodic)

    if need_meter:
        if not readers:
            # Metrics API no-op without readers is fine; still create provider for gauges
            meter_provider = MeterProvider(resource=resource)
        else:
            meter_provider = MeterProvider(resource=resource, metric_readers=readers)
        if readers:
            global _test_metric_reader
            _test_metric_reader = readers[0]
            meter_provider._silverback_metric_reader = readers[0]  # type: ignore[attr-defined]
        metrics.set_meter_provider(meter_provider)
        _meter_provider = meter_provider
    else:
        _meter_provider = existing_meter

    meter = metrics.get_meter("silverback", "0.0.0")
    _bridge = MetricBridge(meter=meter)
    _configured = True
    atexit.register(_shutdown_providers)
    logger.debug("Silverback OpenTelemetry configured")
    return True


def _provider_is_real(provider: Any) -> bool:
    """Proxy/default providers from API package are not 'configured'."""
    name = type(provider).__name__
    return name not in {"ProxyTracerProvider", "ProxyMeterProvider", "NoOpMeterProvider"}


def _build_otlp_span_exporter() -> Any | None:
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT") or os.environ.get(
        "OTEL_EXPORTER_OTLP_ENDPOINT"
    )
    if not endpoint:
        return None
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        return OTLPSpanExporter()
    except ImportError:
        logger.warning("OTLP span exporter not installed; traces will not export")
        return None


def _build_otlp_metric_reader() -> Any | None:
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT") or os.environ.get(
        "OTEL_EXPORTER_OTLP_ENDPOINT"
    )
    if not endpoint:
        return None
    try:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

        return PeriodicExportingMetricReader(OTLPMetricExporter())
    except ImportError:
        logger.warning("OTLP metric exporter not installed; metrics will not export")
        return None


def instrument_broker(broker: "AsyncBroker") -> bool:
    """Attach TaskIQ OpenTelemetryMiddleware via TaskiqInstrumentor."""
    try:
        from taskiq.instrumentation import TaskiqInstrumentor
    except ImportError:
        try:
            # Older layout fallback
            from taskiq.middlewares.opentelemetry_middleware import OpenTelemetryMiddleware

            broker.middlewares.insert(0, OpenTelemetryMiddleware())
            return True
        except ImportError:
            logger.warning("taskiq[opentelemetry] not installed; broker not instrumented")
            return False

    TaskiqInstrumentor().instrument_broker(
        broker,
        tracer_provider=_tracer_provider,
        meter_provider=_meter_provider,
    )
    return True


def create_handler_middleware() -> "TaskiqMiddleware":
    """Return middleware that wraps user handlers in a silverback.handler span."""
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode
    from taskiq import TaskiqMessage, TaskiqMiddleware, TaskiqResult

    class SilverbackOTelMiddleware(TaskiqMiddleware):
        def __init__(self) -> None:
            super().__init__()
            self._tracer = trace.get_tracer("silverback", "0.0.0")
            self._spans: dict[str, Any] = {}

        def pre_execute(self, message: TaskiqMessage) -> TaskiqMessage:
            if not message.labels.get("task_type"):
                return message
            span = self._tracer.start_span(
                HANDLER_SPAN_NAME,
                attributes={
                    "silverback.task_name": message.task_name,
                    "silverback.task_type": message.labels.get("task_type", ""),
                },
            )
            for key in ("block", "txn", "idx", "time", "metric"):
                if key in message.labels:
                    span.set_attribute(f"silverback.{key}", str(message.labels[key]))
            self._spans[message.task_id] = span
            return message

        def post_execute(self, message: TaskiqMessage, result: TaskiqResult) -> None:
            span = self._spans.pop(message.task_id, None)
            if span is None:
                return
            if result.is_err:
                span.set_status(Status(StatusCode.ERROR))
                if result.error:
                    span.record_exception(result.error)
            else:
                span.set_status(Status(StatusCode.OK))
            span.set_attribute("silverback.execution_time_s", result.execution_time)
            span.end()

    return SilverbackOTelMiddleware()


def record_task_metrics(
    task_name: str,
    metrics: dict[str, "Datapoint"],
    *,
    bot_name: str | None = None,
    ecosystem: str | None = None,
    network: str | None = None,
    block_number: int | None = None,
    completed: datetime | None = None,
    notify: bool = False,
) -> list[Coroutine]:
    """Emit Datapoints to OTel MetricBridge. Returns handler coroutines if notify."""
    bridge = get_bridge()
    if bridge is None:
        return []

    attrs: dict[str, Any] = {"silverback.task": task_name}
    if bot_name:
        attrs["silverback.bot"] = bot_name
    if ecosystem:
        attrs["silverback.ecosystem"] = ecosystem
    if network:
        attrs["silverback.network"] = network
    if block_number is not None:
        attrs["silverback.block_number"] = block_number

    coros: list[Coroutine] = []
    for metric_name, datapoint in metrics.items():
        coros.extend(
            bridge.record(
                metric_name,
                datapoint,
                attributes=attrs,
                completed=completed,
                notify=notify,
            )
        )
    return coros


def get_test_span_exporter() -> Any:
    return _test_span_exporter


def get_test_metric_reader() -> Any:
    return _test_metric_reader


def reset_for_tests() -> None:
    """Clear module state between unit tests (including OTel global providers)."""
    global _configured, _bridge, _meter_provider, _tracer_provider
    global _test_span_exporter, _test_metric_reader
    _configured = False
    _bridge = None
    _meter_provider = None
    _tracer_provider = None
    _test_span_exporter = None
    _test_metric_reader = None
    try:
        from opentelemetry import metrics, trace
        from opentelemetry.util._once import Once

        # Allow re-setting providers in subsequent tests
        trace._TRACER_PROVIDER_SET_ONCE = Once()  # type: ignore[attr-defined]
        trace._TRACER_PROVIDER = None  # type: ignore[attr-defined]
        metrics._METER_PROVIDER_SET_ONCE = Once()  # type: ignore[attr-defined]
        metrics._METER_PROVIDER = None  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
