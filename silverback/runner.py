import asyncio
from abc import ABC, abstractmethod
from typing import Optional, Tuple

from ape import chain
from ape.contracts import ContractEvent, ContractInstance
from ape.logging import logger
from ape.utils import ManagerAccessMixin
from ape_ethereum.ecosystem import keccak
from taskiq import AsyncTaskiqDecoratedTask, TaskiqResult

from .application import SilverbackApp
from .exceptions import Halt, NoWebsocketAvailableError
from .persistence import BasePersistentStorage, HandlerResult, init_mongo
from .settings import Settings
from .subscriptions import SubscriptionType, Web3SubscriptionsManager
from .types import SilverbackIdent, handler_id_block, handler_id_event
from .utils import async_wrap_iter, hexbytes_dict

settings = Settings()


class BaseRunner(ABC):
    def __init__(self, app: SilverbackApp, *args, max_exceptions: int = 3, **kwargs):
        self.app = app

        self.max_exceptions = max_exceptions
        self.exceptions = 0
        self.last_block_seen = 0
        self.last_block_processed = 0
        self.persistence: Optional[BasePersistentStorage] = None
        self.ident = SilverbackIdent.from_settings(settings)

    async def _handle_result(
        self, handler_id: str, block_number: int | None, log_index: int | None, result: TaskiqResult
    ):
        if result.is_err:
            self.exceptions += 1

        else:
            self.exceptions = 0

        if self.exceptions > self.max_exceptions:
            raise Halt()

        if self.persistence:
            handler_result = HandlerResult.from_taskiq(
                self.ident, handler_id, block_number, log_index, result
            )

            await self.persistence.add_result(handler_result)

    async def _checkpoint(
        self, last_block_seen: int = 0, last_block_processed: int = 0
    ) -> Tuple[int, int]:
        """Set latest checkpoint block number"""
        if (
            last_block_seen > self.last_block_seen
            or last_block_processed > self.last_block_processed
        ):
            logger.debug(
                (
                    f"Checkpoint block [seen={self.last_block_seen}, "
                    f"procssed={self.last_block_processed}]"
                )
            )
            self.last_block_seen = max(last_block_seen, self.last_block_seen)
            self.last_block_processed = max(last_block_processed, self.last_block_processed)

            self.app.checkpoint(self.last_block_seen, self.last_block_processed)

            if self.persistence:
                await self.persistence.set_instance_state(
                    self.ident, self.last_block_seen, self.last_block_processed
                )

        return self.last_block_seen, self.last_block_processed

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
        if settings.MONGODB_URI:
            self.persistence = await init_mongo(settings.MONGODB_URI)

        if self.persistence:
            boot_state = await self.persistence.get_instance_state(self.ident)
            if boot_state:
                self.last_block_seen = boot_state.last_block_seen
                self.last_block_processed = boot_state.last_block_processed

            # NOTE: This will update TaskIQ state before app startup.
            # TODO: I have no idea if this will work with a distributed runner/worker
            # setup.  That may require our own injected startup task, but it's unclear if
            # we can do that before Taskiq's startup handlers.  We might be able to force
            # SilverbackApplication to decorate an internal func to handle it.  Need to
            # figure that out.
            self.app.checkpoint(self.last_block_seen, self.last_block_processed)

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
            block = self.provider.network.ecosystem.decode_block(hexbytes_dict(raw_block))

            if block.number is not None:
                await self._checkpoint(last_block_seen=block.number)

            block_task = await block_handler.kiq(raw_block)
            result = await block_task.wait_result()

            await self._handle_result(
                handler_id_block(block.number), block.number or 0, None, result
            )

            if block.number is not None:
                await self._checkpoint(last_block_processed=block.number)

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

            if event.block_number is not None:
                await self._checkpoint(last_block_seen=event.block_number)

            event_task = await event_handler.kiq(event)
            result = await event_task.wait_result()
            await self._handle_result(
                handler_id_event(contract_event.contract.address, contract_event.abi.selector),
                event.block_number,
                event.log_index,
                result,
            )

            if event.block_number is not None:
                await self._checkpoint(last_block_processed=event.block_number)

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
            if block.number is not None:
                await self._checkpoint(last_block_seen=block.number)

            block_task = await block_handler.kiq(block)
            result = await block_task.wait_result()
            await self._handle_result(handler_id_block(block.number), block.number, None, result)

            if block.number is not None:
                await self._checkpoint(last_block_processed=block.number)

    async def _event_task(
        self, contract_event: ContractEvent, event_handler: AsyncTaskiqDecoratedTask
    ):
        new_block_timeout = None
        start_block = None
        address = None
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
            if event.block_number is not None:
                await self._checkpoint(last_block_seen=event.block_number)

            event_task = await event_handler.kiq(event)
            result = await event_task.wait_result()
            await self._handle_result(
                # TODO: Under what circumstance can address be None?
                handler_id_event(address, contract_event.abi.selector),
                event.block_number,
                event.log_index,
                result,
            )

            if event.block_number is not None:
                await self._checkpoint(last_block_processed=event.block_number)
