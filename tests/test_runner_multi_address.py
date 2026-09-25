"""Unit checks for multi-address event subscriptions (Silverback #295 + Ape #2757)."""

import asyncio
from unittest.mock import MagicMock, patch

from eth_utils import to_checksum_address
from ethpm_types.abi import EventABI

from silverback.main import TaskData
from silverback.runner import PollingRunner, WebsocketRunner

ADDR_A = to_checksum_address("0x" + "11" * 20)
ADDR_B = to_checksum_address("0x" + "22" * 20)
EVENT_SIG = "Transfer(address indexed from, address indexed to, uint256 value)"


class _EmptyAsyncIter:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


def _event_task_data(addresses: list[str] | None = None) -> TaskData:
    labels: dict[str, str] = {"event": EVENT_SIG}
    if addresses is not None:
        labels["address"] = ",".join(addresses)
    return TaskData(name="on_transfer", labels=labels)


def _run_polling(addresses: list[str] | None):
    runner = PollingRunner.__new__(PollingRunner)
    runner._runtime_task_group = MagicMock()
    runner._runtime_task_group.create_task = MagicMock()
    provider = MagicMock()
    provider.poll_logs.return_value = iter([])
    runner.provider = provider

    with patch("silverback.runner.async_wrap_iter", return_value=_EmptyAsyncIter()):
        asyncio.run(runner._event_task(_event_task_data(addresses)))

    return provider.poll_logs.call_args.kwargs


def test_polling_runner_forwards_multiple_addresses_to_poll_logs():
    kwargs = _run_polling([ADDR_A, ADDR_B])
    assert kwargs["address"] == [ADDR_A, ADDR_B]
    assert len(kwargs["events"]) == 1
    assert isinstance(kwargs["events"][0], EventABI)


def test_polling_runner_forwards_none_address():
    kwargs = _run_polling(None)
    assert kwargs["address"] is None


def test_polling_runner_forwards_single_address_as_list():
    kwargs = _run_polling([ADDR_A])
    assert kwargs["address"] == [ADDR_A]


def test_websocket_runner_subscribes_with_address_list():
    runner = WebsocketRunner.__new__(WebsocketRunner)
    runner._runtime_task_group = MagicMock()
    captured: dict = {}

    async def capture_subscribe(sub):
        captured["sub"] = sub
        return "sub-1"

    runner._web3 = MagicMock()
    runner._web3.subscription_manager.subscribe = capture_subscribe

    asyncio.run(runner._event_task(_event_task_data([ADDR_A, ADDR_B])))

    assert captured["sub"].address == [ADDR_A, ADDR_B]


def test_from_addresses_label_roundtrip():
    """Mirrors main.py label encoding used by @bot.on_(..., from_addresses=...)."""
    addresses = [ADDR_A, ADDR_B]
    label = ",".join(addresses)
    decoded = list(map(to_checksum_address, label.split(",")))
    assert decoded == addresses
