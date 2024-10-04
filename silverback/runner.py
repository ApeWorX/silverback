import asyncio
from abc import ABC, abstractmethod

from ape import chain
from ape.logging import logger
from ape.utils import ManagerAccessMixin
from ape_ethereum.ecosystem import keccak
from ethpm_types import EventABI
from packaging.specifiers import SpecifierSet
from packaging.version import Version
from taskiq import AsyncTaskiqTask
from taskiq.kicker import AsyncKicker

from .application import SilverbackApp, SystemConfig, TaskData
from .exceptions import Halt, NoTasksAvailableError, NoWebsocketAvailableError, StartupFailure
from .recorder import BaseRecorder, TaskResult
from .state import AppDatastore, AppState
from .subscriptions import SubscriptionType, Web3SubscriptionsManager
from .types import TaskType
from .utils import (
    async_wrap_iter,
    hexbytes_dict,
    run_taskiq_task_group_wait_results,
    run_taskiq_task_wait_result,
)


class BaseRunner(ABC):
    def __init__(
        self,
        # TODO: Make fully stateless by replacing `app` with `broker` and `identifier`
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

    def _create_task_kicker(self, task_data: TaskData) -> AsyncKicker:
        return AsyncKicker(
            task_name=task_data.name, broker=self.app.broker, labels=task_data.labels
        )

    def _create_system_task_kicker(self, task_type: TaskType) -> AsyncKicker:
        assert "system:" in str(task_type)
        return self._create_task_kicker(TaskData(name=str(task_type), labels={}))

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
    async def _block_task(self, task_data: TaskData):
        """
        Handle a block_handler task
        """

    @abstractmethod
    async def _event_task(self, task_data: TaskData):
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
        # Initialize broker (run worker startup events)
        await self.app.broker.startup()

        # Obtain system configuration for worker
        result = await run_taskiq_task_wait_result(
            self._create_system_task_kicker(TaskType.SYSTEM_CONFIG)
        )
        if result.is_err or not isinstance(result.return_value, SystemConfig):
            raise StartupFailure("Unable to determine system configuration of worker")

        # NOTE: Increase the specifier set here if there is a breaking change to this
        if Version(result.return_value.sdk_version) not in SpecifierSet(">=0.5.0"):
            # TODO: set to next breaking change release before release
            raise StartupFailure("Worker SDK version too old, please rebuild")

        if not (
            system_tasks := set(TaskType(task_name) for task_name in result.return_value.task_types)
        ):
            raise StartupFailure("No system tasks detected, startup failure")
        # NOTE: Guaranteed to be at least one because of `TaskType.SYSTEM_CONFIG`
        system_tasks_str = "\n- ".join(system_tasks)
        logger.info(
            f"Worker using Silverback SDK v{result.return_value.sdk_version}"
            f", available task types:\n- {system_tasks_str}"
        )

        # NOTE: Do this for other system tasks because they may not be in older SDK versions
        #       `if TaskType.<SYSTEM_TASK_NAME> not in system_tasks: raise StartupFailure(...)`
        #       or handle accordingly by having default logic if it is not available

        # Initialize recorder (if available) and fetch state if app has been run previously
        if self.recorder:
            await self.recorder.init(app_id=self.app.identifier)

        if startup_state := (await self.datastore.init(app_id=self.app.identifier)):
            self.state = startup_state

        else:  # use empty state
            self.state = AppState(last_block_seen=-1, last_block_processed=-1)

        # Execute Silverback startup task before we init the rest
        startup_taskdata_result = await run_taskiq_task_wait_result(
            self._create_system_task_kicker(TaskType.SYSTEM_USER_TASKDATA), TaskType.STARTUP
        )

        if startup_taskdata_result.is_err:
            raise StartupFailure(startup_taskdata_result.error)

        else:
            startup_task_handlers = map(
                self._create_task_kicker, startup_taskdata_result.return_value
            )

            startup_task_results = await run_taskiq_task_group_wait_results(
                (task_handler for task_handler in startup_task_handlers), self.state
            )

            if any(result.is_err for result in startup_task_results):
                # NOTE: Abort before even starting to run
                raise StartupFailure(
                    *(result.error for result in startup_task_results if result.is_err)
                )

            elif self.recorder:
                converted_results = map(TaskResult.from_taskiq, startup_task_results)
                await asyncio.gather(*(self.recorder.add_result(r) for r in converted_results))

            # NOTE: No need to handle results otherwise

        # Create our long-running event listeners
        new_block_taskdata_results = await run_taskiq_task_wait_result(
            self._create_system_task_kicker(TaskType.SYSTEM_USER_TASKDATA), TaskType.NEW_BLOCK
        )
        if new_block_taskdata_results.is_err:
            raise StartupFailure(new_block_taskdata_results.error)

        event_log_taskdata_results = await run_taskiq_task_wait_result(
            self._create_system_task_kicker(TaskType.SYSTEM_USER_TASKDATA), TaskType.EVENT_LOG
        )
        if event_log_taskdata_results.is_err:
            raise StartupFailure(event_log_taskdata_results.error)

        if (
            len(new_block_taskdata_results.return_value)
            == len(event_log_taskdata_results.return_value)
            == 0  # Both are empty
        ):
            raise NoTasksAvailableError()

        # NOTE: Any propagated failure in here should be handled such that shutdown tasks also run
        # TODO: `asyncio.TaskGroup` added in Python 3.11
        listener_tasks = (
            *(
                asyncio.create_task(self._block_task(task_def))
                for task_def in new_block_taskdata_results.return_value
            ),
            *(
                asyncio.create_task(self._event_task(task_def))
                for task_def in event_log_taskdata_results.return_value
            ),
        )

        # NOTE: Safe to do this because no tasks were actually scheduled to run
        if len(listener_tasks) == 0:
            raise NoTasksAvailableError()

        # Run until one task bubbles up an exception that should stop execution
        tasks_with_errors, tasks_running = await asyncio.wait(
            listener_tasks, return_when=asyncio.FIRST_EXCEPTION
        )
        if runtime_errors := "\n".join(str(task.exception()) for task in tasks_with_errors):
            # NOTE: In case we are somehow not displaying the error correctly with task status
            logger.warning(f"Runtime error(s) detected, shutting down:\n{runtime_errors}")

        # Cancel any still running
        for task in tasks_running:
            task.cancel()

        # NOTE: All listener tasks are shut down now

        # Execute Silverback shutdown task(s) before shutting down the broker and app
        shutdown_taskdata_result = await run_taskiq_task_wait_result(
            self._create_system_task_kicker(TaskType.SYSTEM_USER_TASKDATA), TaskType.SHUTDOWN
        )

        if shutdown_taskdata_result.is_err:
            raise StartupFailure(shutdown_taskdata_result.error)

        else:
            shutdown_task_handlers = map(
                self._create_task_kicker, shutdown_taskdata_result.return_value
            )

            shutdown_task_results = await run_taskiq_task_group_wait_results(
                (task_handler for task_handler in shutdown_task_handlers)
            )

            if any(result.is_err for result in shutdown_task_results):
                errors_str = "\n".join(
                    str(result.error) for result in shutdown_task_results if result.is_err
                )
                logger.error(f"Errors while shutting down:\n{errors_str}")

            elif self.recorder:
                converted_results = map(TaskResult.from_taskiq, shutdown_task_results)
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
        if not (ws_uri := self.chain_manager.provider.ws_uri):
            raise NoWebsocketAvailableError()

        self.ws_uri = ws_uri

    async def _block_task(self, task_data: TaskData):
        new_block_task_kicker = self._create_task_kicker(task_data)
        sub_id = await self.subscriptions.subscribe(SubscriptionType.BLOCKS)
        logger.debug(f"Handling blocks via {sub_id}")

        async for raw_block in self.subscriptions.get_subscription_data(sub_id):
            block = self.provider.network.ecosystem.decode_block(hexbytes_dict(raw_block))

            await self._checkpoint(last_block_seen=block.number)
            await self._handle_task(await new_block_task_kicker.kiq(raw_block))
            await self._checkpoint(last_block_processed=block.number)

    async def _event_task(self, task_data: TaskData):
        if not (contract_address := task_data.labels.get("contract_address")):
            raise StartupFailure("Contract instance required.")

        if not (event_signature := task_data.labels.get("event_signature")):
            raise StartupFailure("No Event Signature provided.")

        event_abi = EventABI.from_signature(event_signature)

        event_log_task_kicker = self._create_task_kicker(task_data)

        sub_id = await self.subscriptions.subscribe(
            SubscriptionType.EVENTS,
            address=contract_address,
            topics=["0x" + keccak(text=event_abi.selector).hex()],
        )
        logger.debug(f"Handling '{contract_address}:{event_abi.name}' logs via {sub_id}")

        async for raw_event in self.subscriptions.get_subscription_data(sub_id):
            event = next(  # NOTE: `next` is okay since it only has one item
                self.provider.network.ecosystem.decode_logs([raw_event], event_abi)
            )

            await self._checkpoint(last_block_seen=event.block_number)
            await self._handle_task(await event_log_task_kicker.kiq(event))
            await self._checkpoint(last_block_processed=event.block_number)

    async def run(self):
        async with Web3SubscriptionsManager(self.ws_uri) as subscriptions:
            self.subscriptions = subscriptions
            await super().run()


class PollingRunner(BaseRunner, ManagerAccessMixin):
    """
    Run a single app against a live network using a basic in-memory queue.
    """

    # TODO: Move block_timeout settings to Ape core config
    # TODO: Merge polling/websocket subscriptions downstream in Ape core

    def __init__(self, app: SilverbackApp, *args, **kwargs):
        super().__init__(app, *args, **kwargs)
        logger.warning(
            "The polling runner makes a significant amount of requests. "
            "Do not use in production over long time periods unless you know what you're doing."
        )

    async def _block_task(self, task_data: TaskData):
        new_block_task_kicker = self._create_task_kicker(task_data)

        if block_settings := self.app.poll_settings.get("_blocks_"):
            new_block_timeout = block_settings.get("new_block_timeout")
        else:
            new_block_timeout = None

        new_block_timeout = (
            new_block_timeout if new_block_timeout is not None else self.app.new_block_timeout
        )
        async for block in async_wrap_iter(
            chain.blocks.poll_blocks(
                # NOTE: No start block because we should begin polling from head
                new_block_timeout=new_block_timeout,
            )
        ):
            await self._checkpoint(last_block_seen=block.number)
            await self._handle_task(await new_block_task_kicker.kiq(block))
            await self._checkpoint(last_block_processed=block.number)

    async def _event_task(self, task_data: TaskData):
        if not (contract_address := task_data.labels.get("contract_address")):
            raise StartupFailure("Contract instance required.")

        if not (event_signature := task_data.labels.get("event_signature")):
            raise StartupFailure("No Event Signature provided.")

        event_abi = EventABI.from_signature(event_signature)

        event_log_task_kicker = self._create_task_kicker(task_data)
        if address_settings := self.app.poll_settings.get(contract_address):
            new_block_timeout = address_settings.get("new_block_timeout")
        else:
            new_block_timeout = None

        new_block_timeout = (
            new_block_timeout if new_block_timeout is not None else self.app.new_block_timeout
        )
        async for event in async_wrap_iter(
            self.provider.poll_logs(
                # NOTE: No start block because we should begin polling from head
                address=contract_address,
                new_block_timeout=new_block_timeout,
                events=[event_abi],
            )
        ):
            await self._checkpoint(last_block_seen=event.block_number)
            await self._handle_task(await event_log_task_kicker.kiq(event))
            await self._checkpoint(last_block_processed=event.block_number)
