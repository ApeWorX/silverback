import asyncio
from abc import ABC, abstractmethod
from typing import Coroutine

from ape import chain
from ape.contracts import ContractEvent, ContractInstance
from ape.logging import logger
from taskiq import AsyncTaskiqDecoratedTask, TaskiqResult

from .application import SilverBackApp
from .exceptions import Halt, SilverBackException
from .utils import async_wrap_iter


class BaseRunner(ABC):
    def __init__(self, app: SilverBackApp, *args, max_exceptions: int = 3, **kwargs):
        self.app = app

        self.max_exceptions = max_exceptions
        self.exceptions = 0

    def _handle_result(self, result: TaskiqResult):
        if result.is_err:
            self.exceptions += 1

        else:
            self.exceptions = 0

        if self.exceptions > self.max_exceptions:
            raise Halt()

    @abstractmethod
    async def _block_task(self, block_handler: AsyncTaskiqDecoratedTask):
        """
        Handle a block_handler task
        """

    @abstractmethod
    async def _event_task(
        self, contract_event: ContractEvent, event_handler: AsyncTaskiqDecoratedTask
    ):
        """
        handle an event handler task for the given contract event
        """

    async def run(self, *other_tasks: Coroutine):
        await self.app.broker.startup()

        if block_handler := self.app.get_block_handler():
            tasks = [self._block_task(block_handler)]
        else:
            tasks = []

        for contract_address in self.app.contract_events:
            for event_name, contract_event in self.app.contract_events[contract_address].items():
                if event_handler := self.app.get_event_handler(contract_address, event_name):
                    tasks.append(self._event_task(contract_event, event_handler))

        tasks.extend(other_tasks)

        if len(tasks) == 0:
            raise SilverBackException("No tasks to execute")

        await asyncio.gather(*tasks)

        await self.app.broker.shutdown()


class LiveRunner(BaseRunner):
    """
    Run a single app against a live network using a basic in-memory queue.
    """

    def __init__(self, app: SilverBackApp, *args, **kwargs):
        super().__init__(app, *args, **kwargs)
        logger.info(f"Using {self.__class__.__name__}: max_exceptions={self.max_exceptions}")

    async def _block_task(self, block_handler: AsyncTaskiqDecoratedTask):
        new_block_timeout = None
        start_block = None
        if "_blocks_" in self.app.poll_settings:
            block_settings = self.app.poll_settings["_blocks_"]
            new_block_timeout = block_settings.get("new_block_timeout")
            start_block = block_settings.get("start_block")

        new_block_timeout = (
            new_block_timeout if new_block_timeout is not None else self.app.new_block_timeout
        )
        start_block = start_block if start_block is not None else self.app.start_block
        async for block in async_wrap_iter(
            chain.blocks.poll_blocks(start_block=start_block, new_block_timeout=new_block_timeout)
        ):
            block_task = await block_handler.kiq(block)
            result = await block_task.wait_result()
            self._handle_result(result)

    async def _event_task(
        self, contract_event: ContractEvent, event_handler: AsyncTaskiqDecoratedTask
    ):
        new_block_timeout = None
        start_block = None
        if isinstance(contract_event.contract, ContractInstance):
            address = contract_event.contract.address
            if address in self.app.poll_settings:
                address_settings = self.app.poll_settings[address]
                new_block_timeout = address_settings.get("new_block_timeout")
                start_block = address_settings.get("start_block")

        new_block_timeout = (
            new_block_timeout if new_block_timeout is not None else self.app.new_block_timeout
        )
        start_block = start_block if start_block is not None else self.app.start_block
        async for event in async_wrap_iter(
            contract_event.poll_logs(start_block=start_block, new_block_timeout=new_block_timeout)
        ):
            event_task = await event_handler.kiq(event)
            result = await event_task.wait_result()
            self._handle_result(result)
