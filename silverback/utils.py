import asyncio
import threading
from typing import AsyncIterator, Iterable, Iterator

from ape.types import HexBytes
from taskiq import AsyncTaskiqDecoratedTask, TaskiqResult
from taskiq.kicker import AsyncKicker


async def run_taskiq_task_wait_result(
    task_def: AsyncTaskiqDecoratedTask | AsyncKicker, *args, **kwargs
) -> TaskiqResult:
    task = await task_def.kiq(*args, **kwargs)
    return await task.wait_result()


async def run_taskiq_task_group_wait_results(
    task_defs: Iterable[AsyncTaskiqDecoratedTask | AsyncKicker], *args, **kwargs
) -> list[TaskiqResult]:
    tasks = await asyncio.gather(*(task_def.kiq(*args, **kwargs) for task_def in task_defs))
    return await asyncio.gather(*(task.wait_result() for task in tasks))


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


def hexbytes_dict(data: dict, recurse_count: int = 0) -> dict:
    """Converts any hex string values in a flat dictionary to HexBytes."""
    fixed_data = {}

    for name, value in data.items():
        if isinstance(value, str) and value.startswith("0x"):
            fixed_data[name] = HexBytes(value)
        elif isinstance(value, dict):
            if recurse_count > 3:
                raise RecursionError("Event object is too deep")
            hexbytes_dict(value, recurse_count + 1)
        else:
            fixed_data[name] = value

    return fixed_data
