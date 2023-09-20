import asyncio
from abc import ABC, abstractmethod

from ape import chain
from ape.contracts import ContractEvent, ContractInstance
from ape.logging import logger
from ape.utils import ManagerAccessMixin
from ape_ethereum.ecosystem import keccak
from taskiq import AsyncTaskiqDecoratedTask, TaskiqResult

from .application import SilverbackApp
from .exceptions import Halt, NoWebsocketAvailableError
from .subscriptions import SubscriptionType, Web3SubscriptionsManager
from .utils import async_wrap_iter


class BaseRunner(ABC):
    def __init__(self, app: SilverbackApp, *args, max_exceptions: int = 3, **kwargs):
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
        """
        Run the task broker client for the assembled ``SilverbackApp`` application.

        Will listen for events against the connected provider (using `ManagerAccessMixin` context),
        and process them by kicking events over to the configured broker.

        Raises:
            :class:`~silverback.exceptions.Halt`: If there are no configured tasks to execute.
        """
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
            raise Halt("No tasks to execute")

        await asyncio.gather(*tasks)

        await self.app.broker.shutdown()


class WebsocketRunner(BaseRunner, ManagerAccessMixin):
    """
    Run a single app against a live network using a basic in-memory queue and websockets.
    """

    def __init__(self, app: SilverbackApp, *args, **kwargs):
        super().__init__(app, *args, **kwargs)
        logger.info(f"Using {self.__class__.__name__}: max_exceptions={self.max_exceptions}")

        # Check for websocket support
        if not (ws_uri := app.chain_manager.provider.ws_uri):
            raise NoWebsocketAvailableError()

        self.ws_uri = ws_uri

    async def _block_task(self, block_handler: AsyncTaskiqDecoratedTask):
        sub_id = await self.subscriptions.subscribe(SubscriptionType.BLOCKS)
        logger.debug(f"Handling blocks via {sub_id}")

        async for raw_block in self.subscriptions.get_subscription_data(sub_id):
            block_task = await block_handler.kiq(raw_block)
            result = await block_task.wait_result()
            self._handle_result(result)

    async def _event_task(
        self, contract_event: ContractEvent, event_handler: AsyncTaskiqDecoratedTask
    ):
        if not isinstance(contract_event.contract, ContractInstance):
            # For type-checking.
            raise ValueError("Contract instance required.")

        sub_id = await self.subscriptions.subscribe(
            SubscriptionType.EVENTS,
            address=contract_event.contract.address,
            topics=["0x" + keccak(text=contract_event.abi.selector).hex()],
        )
        logger.debug(f"Handling '{contract_event.name}' events via {sub_id}")

        async for raw_event in self.subscriptions.get_subscription_data(sub_id):
            event = next(  # NOTE: `next` is okay since it only has one item
                self.provider.network.ecosystem.decode_logs(
                    [raw_event],
                    contract_event.abi,
                )
            )
            event_task = await event_handler.kiq(event)
            result = await event_task.wait_result()
            self._handle_result(result)

    async def run(self):
        async with Web3SubscriptionsManager(self.ws_uri) as subscriptions:
            self.subscriptions = subscriptions
            await super().run()


class PollingRunner(BaseRunner):
    """
    Run a single app against a live network using a basic in-memory queue.
    """

    def __init__(self, app: SilverbackApp, *args, **kwargs):
        super().__init__(app, *args, **kwargs)
        logger.warning(
            "The polling runner makes a significant amount of requests. "
            "Do not use in production over long time periods unless you know what you're doing."
        )

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
