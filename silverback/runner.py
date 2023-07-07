import asyncio
from abc import ABC, abstractmethod

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

    async def run(self):
        await self.app.broker.startup()

        if block_handler := self.app.get_block_handler():
            tasks = [self._block_task(block_handler)]
        else:
            tasks = []

        for contract_address in self.app.contract_events:
            for event_name, contract_event in self.app.contract_events[contract_address].items():
                if event_handler := self.app.get_event_handler(contract_address, event_name):
                    tasks.append(self._event_task(contract_event, event_handler))

        if len(tasks) == 0:
            raise SilverBackException("No tasks to execute")

        try:
            await asyncio.gather(*tasks)
        except Exception as e:
            logger.error(f"Critical exception: {e}")

        finally:
            await self.app.broker.shutdown()


class LiveRunner(BaseRunner):
    """
    Run a single app against a live network using a basic in-memory queue.
    """

    def __init__(self, app: SilverBackApp, *args, **kwargs):
        super().__init__(app, *args, **kwargs)
        logger.info(f"Using {self.__class__.__name__}: max_exceptions={self.max_exceptions}")

    async def _block_task(self, block_handler: AsyncTaskiqDecoratedTask):
        async for block in async_wrap_iter(
            chain.blocks.poll_blocks(new_block_timeout=self.app.new_block_timeout)
        ):
            block_task = await block_handler.kiq(block)
            result = await block_task.wait_result()
            self._handle_result(result)

    async def _event_task(
        self, contract_event: ContractEvent, event_handler: AsyncTaskiqDecoratedTask
    ):
        new_block_timeout = None
        if isinstance(contract_event.contract, ContractInstance):
            address = contract_event.contract.address
            if (
                address in self.app.poll_settings
                and "new_block_timeout" in self.app.poll_settings[address]
            ):
                new_block_timeout = self.app.poll_settings[address]["new_block_timeout"]

        new_block_timeout = new_block_timeout if new_block_timeout is not None else self.app.new_block_timeout
        async for event in async_wrap_iter(
            contract_event.poll_logs(new_block_timeout=new_block_timeout)
        ):
            event_task = await event_handler.kiq(event)
            result = await event_task.wait_result()
            self._handle_result(result)
