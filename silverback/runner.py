import asyncio
from abc import ABC, abstractmethod
from typing import Optional

from ape import chain
from ape.contracts import ContractEvent, ContractInstance
from ape.logging import logger
from ape.utils import ManagerAccessMixin
from ape_ethereum.ecosystem import keccak
from taskiq import AsyncTaskiqDecoratedTask, AsyncTaskiqTask

from .application import SilverbackApp
from .exceptions import Halt, NoWebsocketAvailableError
from .recorder import BaseRecorder, TaskResult
from .subscriptions import SubscriptionType, Web3SubscriptionsManager
from .types import AppState, SilverbackID, TaskType
from .utils import async_wrap_iter, hexbytes_dict


class BaseRunner(ABC):
    def __init__(
        self,
        app: SilverbackApp,
        *args,
        max_exceptions: int = 3,
        recorder: Optional[BaseRecorder] = None,
        **kwargs,
    ):
        self.app = app
        self.recorder = recorder

        self.max_exceptions = max_exceptions
        self.exceptions = 0

        ecosystem_name, network_name = app.network_choice.split(":")
        self.identifier = SilverbackID(
            name=app.name,
            ecosystem=ecosystem_name,
            network=network_name,
        )

        logger.info(f"Using {self.__class__.__name__}: max_exceptions={self.max_exceptions}")

    async def _handle_task(self, task: AsyncTaskiqTask):
        result = await task.wait_result()

        if self.recorder:
            await self.recorder.add_result(TaskResult.from_taskiq(result))

        if not result.is_err:
            # NOTE: Reset exception counter
            self.exceptions = 0
            return

        self.exceptions += 1

        if self.exceptions > self.max_exceptions or isinstance(result.error, Halt):
            result.raise_for_error()

    async def _checkpoint(
        self,
        last_block_seen: Optional[int] = None,
        last_block_processed: Optional[int] = None,
    ):
        """Set latest checkpoint block number"""
        assert self.app.state, f"{self.__class__.__name__}.run() not triggered."

        logger.debug(
            (
                f"Checkpoint block [seen={self.app.state.last_block_seen}, "
                f"procssed={self.app.state.last_block_processed}]"
            )
        )

        if last_block_seen:
            self.app.state.last_block_seen = last_block_seen
        if last_block_processed:
            self.app.state.last_block_processed = last_block_processed

        if self.recorder:
            try:
                await self.recorder.set_state(self.app.state)

            except Exception as err:
                logger.error(f"Error setting state: {err}")

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
        # Initialize recorder (if available) and fetch state if app has been run previously
        if self.recorder and (startup_state := (await self.recorder.init(app_id=self.identifier))):
            self.app.state = startup_state

        else:  # use empty state
            self.app.state = AppState(last_block_seen=-1, last_block_processed=-1)

        # Initialize broker (run worker startup events)
        await self.app.broker.startup()

        # Execute Silverback startup task before we init the rest
        for startup_task in self.app.tasks[TaskType.STARTUP]:
            task = await startup_task.handler.kiq(
                SilverbackStartupState(
                    last_block_seen=self.last_block_seen,
                    last_block_processed=self.last_block_processed,
                )
            )
            result = await task.wait_result()
            self._handle_result(result)

        tasks = []
        for task in self.app.tasks[TaskType.NEW_BLOCKS]:
            tasks.append(self._block_task(task.handler))

        for task in self.app.tasks[TaskType.EVENT_LOG]:
            tasks.append(self._event_task(task.container, task.handler))

        if len(tasks) == 0:
            raise Halt("No tasks to execute")

        try:
            await asyncio.gather(*tasks)
        except Exception as e:
            logger.error(f"Fatal error detected, shutting down: '{e}'")

        # Execute Silverback shutdown task before shutting down the broker
        for shutdown_task in self.app.tasks[TaskType.SHUTDOWN]:
            task = await shutdown_task.handler.kiq()
            result = self._handle_result(await task.wait_result())

        await self.app.broker.shutdown()


class WebsocketRunner(BaseRunner, ManagerAccessMixin):
    """
    Run a single app against a live network using a basic in-memory queue and websockets.
    """

    def __init__(self, app: SilverbackApp, *args, **kwargs):
        super().__init__(app, *args, **kwargs)

        # Check for websocket support
        if not (ws_uri := app.chain_manager.provider.ws_uri):
            raise NoWebsocketAvailableError()

        self.ws_uri = ws_uri

    async def _block_task(self, block_handler: AsyncTaskiqDecoratedTask):
        sub_id = await self.subscriptions.subscribe(SubscriptionType.BLOCKS)
        logger.debug(f"Handling blocks via {sub_id}")

        async for raw_block in self.subscriptions.get_subscription_data(sub_id):
            block = self.provider.network.ecosystem.decode_block(hexbytes_dict(raw_block))

            await self._checkpoint(last_block_seen=block.number)
            await self._handle_task(await block_handler.kiq(raw_block))
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
        logger.debug(
            f"Handling '{contract_event.contract.address}:{contract_event.name}' logs via {sub_id}"
        )

        async for raw_event in self.subscriptions.get_subscription_data(sub_id):
            event = next(  # NOTE: `next` is okay since it only has one item
                self.provider.network.ecosystem.decode_logs(
                    [raw_event],
                    contract_event.abi,
                )
            )

            await self._checkpoint(last_block_seen=event.block_number)
            await self._handle_task(await event_handler.kiq(event))
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
            await self._checkpoint(last_block_seen=block.number)
            await self._handle_task(await block_handler.kiq(block))
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
            await self._checkpoint(last_block_seen=event.block_number)
            await self._handle_task(await event_handler.kiq(event))
            await self._checkpoint(last_block_processed=event.block_number)
