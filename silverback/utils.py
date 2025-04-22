import asyncio
import threading
from typing import Any, AsyncIterator, Iterator

from ape.types import HexBytes
from eth_typing import HexStr
from eth_utils import to_hex

Topic = list[HexStr] | HexStr | None


def encode_topics_to_string(topics: list[Topic]) -> str:
    """Encode a topic list to a string, for TaskIQ label"""
    # See https://web3py.readthedocs.io/en/stable/filters.html#event-log-filters
    return ";".join(",".join(t) if isinstance(t, list) else t or "" for t in topics)


def _simplify_topic(topic: Topic) -> Topic:
    if isinstance(topic, list) and len(topic) == 1:
        return topic[0]

    return topic


def _clean_trailing_nones(topics: list[Topic]) -> list[Topic]:
    while len(topics) > 0 and topics[-1] is None:
        topics = topics[:-1]

    return topics


def decode_topics_from_string(encoded_topics: str) -> list[Topic]:
    """Decode a topic list from a TaskIQ label into Web3py topics"""
    # NOTE: Should reverse the above
    return _clean_trailing_nones(
        [
            _simplify_topic([to_hex(hexstr=t) for t in et.split(",")]) if et else None
            for et in encoded_topics.split(";")
        ]
    )


def async_wrap_iter(it: Iterator) -> AsyncIterator:
    """Wrap blocking iterator into an asynchronous one"""
    loop = asyncio.get_event_loop()
    q: asyncio.Queue = asyncio.Queue(1)
    exception = None
    _END = object()

    async def yield_queue_items():
        while True:
            next_item = await q.get()
            if next_item is _END:
                break
            yield next_item
        if exception is not None:
            # the iterator has raised, propagate the exception
            raise exception

    def iter_to_queue():
        nonlocal exception
        try:
            for item in it:
                # This runs outside the event loop thread, so we
                # must use thread-safe API to talk to the queue.
                asyncio.run_coroutine_threadsafe(q.put(item), loop).result()
        except Exception as e:
            exception = e
        finally:
            asyncio.run_coroutine_threadsafe(q.put(_END), loop).result()

    threading.Thread(target=iter_to_queue).start()
    return yield_queue_items()


# TODO: Necessary because bytes/HexBytes doesn't encode/decode well for some reason
def clean_hexbytes_dict(data: dict, recurse_count: int = 0) -> dict:
    """Strips `HexBtes` objects from dictionary values, as they do not encode well"""
    fixed_data: dict[str, Any] = {}
    for name, value in data.items():
        if isinstance(value, bytes):
            fixed_data[name] = to_hex(value)

        elif isinstance(value, list):
            fixed_data[name] = [to_hex(v) if isinstance(v, bytes) else v for v in value]

        elif isinstance(value, dict):
            if recurse_count > 3:
                raise RecursionError("object is too deep")

            fixed_data[name] = clean_hexbytes_dict(value, recurse_count + 1)

        else:
            fixed_data[name] = value

    return fixed_data


def parse_hexbytes_dict(data: dict, recurse_count: int = 0) -> dict:
    """Converts any hex string values in a flat dictionary to HexBytes."""
    # NOTE: Reverses above
    fixed_data = {}

    for name, value in data.items():
        if isinstance(value, str) and value.startswith("0x"):
            fixed_data[name] = HexBytes(value)

        elif isinstance(value, list):
            fixed_data[name] = [
                HexBytes(v) if isinstance(value, str) and value.startswith("0x") else v
                for v in value
            ]

        elif isinstance(value, dict):
            if recurse_count > 3:
                raise RecursionError("object is too deep")

            parse_hexbytes_dict(value, recurse_count + 1)

        else:
            fixed_data[name] = value

    return fixed_data
