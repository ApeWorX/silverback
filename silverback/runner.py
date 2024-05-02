import asyncio
from abc import ABC, abstractmethod

from ape import chain
from ape.contracts import ContractEvent, ContractInstance
from ape.logging import logger
from ape.utils import ManagerAccessMixin
from ape_ethereum.ecosystem import keccak
from taskiq import AsyncTaskiqDecoratedTask, AsyncTaskiqTask

from .application import SilverbackApp
from .exceptions import Halt, NoTasksAvailableError, NoWebsocketAvailableError, StartupFailure
from .recorder import BaseRecorder, TaskResult
from .state import AppDatastore, AppState
from .subscriptions import SubscriptionType, Web3SubscriptionsManager
from .types import TaskType
from .utils import async_wrap_iter, hexbytes_dict


class BaseRunner(ABC):
    def __init__(
        self,
        app: SilverbackApp,
        *args,
        max_exceptions: int = 3,
        recorder: BaseRecorder | None = None,
        **kwargs,
    ):
        self.app = app
        self.recorder = recorder
        self.state = None
        self.datastore = AppDatastore()

        self.max_exceptions = max_exceptions
        self.exceptions = 0

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
        last_block_seen: int | None = None,
        last_block_processed: int | None = None,
    ):
        """Set latest checkpoint block number"""
        assert self.state, f"{self.__class__.__name__}.run() not triggered."

        logger.debug(
            (
                f"Checkpoint block [seen={self.state.last_block_seen}, "
                f"procssed={self.state.last_block_processed}]"
            )
        )

        if last_block_seen:
            self.state.last_block_seen = last_block_seen
        if last_block_processed:
            self.state.last_block_processed = last_block_processed

        if self.recorder:
            try:
                await self.datastore.set_state(self.state)

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
            :class:`~silverback.exceptions.StartupFailure`:
                If there was an exception during startup.
            :class:`~silverback.exceptions.NoTasksAvailableError`:
                If there are no configured tasks to execute.
        """
        # Initialize recorder (if available) and fetch state if app has been run previously
        if self.recorder:
            await self.recorder.init(app_id=self.app.identifier)

        if startup_state := (await self.datastore.init(app_id=self.app.identifier)):
            self.state = startup_state

        else:  # use empty state
            self.state = AppState(last_block_seen=-1, last_block_processed=-1)

        # Initialize broker (run worker startup events)
        await self.app.broker.startup()

        # Execute Silverback startup task before we init the rest
        if startup_tasks := await asyncio.gather(
            *(task_def.handler.kiq(self.state) for task_def in self.app.tasks[TaskType.STARTUP])
        ):
            results = await asyncio.gather(
                *(startup_task.wait_result() for startup_task in startup_tasks)
            )

            if any(result.is_err for result in results):
                # NOTE: Abort before even starting to run
                raise StartupFailure(*(result.error for result in results if result.is_err))

            elif self.recorder:
                converted_results = map(TaskResult.from_taskiq, results)
                await asyncio.gather(*(self.recorder.add_result(r) for r in converted_results))

            # NOTE: No need to handle results otherwise

        # Create our long-running event listeners
        # NOTE: Any propagated failure in here should be handled such that shutdown tasks also run
        # TODO: `asyncio.TaskGroup` added in Python 3.11
        listener_tasks = (
            *(
                asyncio.create_task(self._block_task(task_def.handler))
                for task_def in self.app.tasks[TaskType.NEW_BLOCKS]
            ),
            *(
                asyncio.create_task(self._event_task(task_def.container, task_def.handler))
                for task_def in self.app.tasks[TaskType.EVENT_LOG]
            ),
        )

        # NOTE: Safe to do this because no tasks have been scheduled to run yet
        if len(listener_tasks) == 0:
            raise NoTasksAvailableError()

        # Run until one task bubbles up an exception that should stop execution
        tasks_with_errors, tasks_running = await asyncio.wait(
            listener_tasks, return_when=asyncio.FIRST_EXCEPTION
        )
        if runtime_errors := "\n".join(str(task.exception()) for task in tasks_with_errors):
            # NOTE: In case we are somehow not displaying the error correctly with task status
            logger.debug(f"Runtime error(s) detected, shutting down:\n{runtime_errors}")

        # Cancel any still running
        (task.cancel() for task in tasks_running)
        # NOTE: All listener tasks are shut down now

        # Execute Silverback shutdown task(s) before shutting down the broker and app
        if shutdown_tasks := await asyncio.gather(
            *(task_def.handler.kiq() for task_def in self.app.tasks[TaskType.SHUTDOWN])
        ):
            asyncio.gather(*(shutdown_task.is_ready() for shutdown_task in shutdown_tasks))
            if any(result.is_err for result in results):
                errors_str = "\n".join(str(result.error) for result in results if result.is_err)
                logger.error(f"Errors while shutting down:\n{errors_str}")

            elif self.recorder:
                converted_results = map(TaskResult.from_taskiq, results)
                await asyncio.gather(*(self.recorder.add_result(r) for r in converted_results))

            # NOTE: No need to handle results otherwise

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
