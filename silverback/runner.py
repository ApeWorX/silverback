import asyncio
import signal
import sys
from abc import ABC, abstractmethod
from datetime import timedelta
from typing import Any, Coroutine, Type

import pycron  # type: ignore[import-untyped]
import quattro
from ape import chain
from ape.logging import logger
from ape.utils import ManagerAccessMixin
from ape_ethereum.ecosystem import keccak
from eth_utils import to_hex
from ethpm_types import EventABI
from packaging.specifiers import SpecifierSet
from packaging.version import Version
from pydantic import TypeAdapter
from taskiq import AsyncTaskiqDecoratedTask
from web3 import AsyncWeb3, WebSocketProvider
from web3.utils.subscriptions import (
    LogsSubscription,
    LogsSubscriptionContext,
    NewHeadsSubscription,
    NewHeadsSubscriptionContext,
)

from .exceptions import (
    Halt,
    NoTasksAvailableError,
    NoWebsocketAvailableError,
    StartupFailure,
    UnregisteredTask,
)
from .main import SilverbackBot, TaskData
from .recorder import BaseRecorder, TaskResult
from .state import Datastore, StateSnapshot
from .types import TaskType, utc_now
from .utils import async_wrap_iter

if sys.version_info < (3, 11):
    from exceptiongroup import ExceptionGroup


class BaseRunner(ABC):
    def __init__(
        self,
        # TODO: Make fully stateless by replacing `bot` with `broker` and `identifier`
        bot: SilverbackBot,
        *args,
        max_exceptions: int = 3,
        recorder: BaseRecorder | None = None,
        **kwargs,
    ):
        self.bot = bot

        # TODO: Make datastore optional and settings-driven
        # TODO: Allow configuring datastore class
        self.datastore = Datastore()
        self.recorder = recorder

        self.max_exceptions = max_exceptions
        self.exceptions = 0

        logger.info(f"Using {self.__class__.__name__}: max_exceptions={self.max_exceptions}")

    def get_task(self, task_name: str) -> AsyncTaskiqDecoratedTask:
        if not (task := self.bot.broker.find_task(task_name)):
            raise UnregisteredTask(task_name)

        return task

    async def run_system_task(
        self,
        task_type: TaskType,
        *args: Any,
        raise_on_error: bool = True,
    ) -> Any:
        system_task_kicker = self.get_task(task_type.value)
        system_task = await system_task_kicker.kiq(*args)

        if (result := await system_task.wait_result()).is_err:
            if raise_on_error:
                raise StartupFailure(f"System Task Failure [{task_type.name}]: {result.error}")

            else:
                logger.error(f"System Task Failure [{task_type.name}]: {result.error}")
                return

        # HACK: Don't understand why this is failing to work properly in TaskIQ
        return_type: Type | None = system_task_kicker.__annotations__.get("return")
        return TypeAdapter(return_type).validate_python(result.return_value)

    async def run_task(self, task_data: TaskData, *args):
        task = await self.get_task(task_data.name).kiq(*args)
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
        if not self._snapshotting_supported:
            return  # Can't support this feature

        elif snapshot := await self.run_system_task(
            TaskType.SYSTEM_CREATE_SNAPSHOT,
            last_block_seen,
            last_block_processed,
            raise_on_error=False,
        ):
            await self.datastore.save(snapshot)

    async def _cron_tasks(self, cron_tasks: list[TaskData]):
        """
        Handle all cron tasks
        """

        while True:
            # NOTE: Sleep until next exact time boundary (every minute)
            current_time = utc_now()
            wait_time = timedelta(
                seconds=60 - 1 - current_time.second,
                microseconds=int(1e6) - current_time.microsecond,
            )
            await asyncio.sleep(wait_time.total_seconds())
            current_time += wait_time

            for task_data in cron_tasks:
                if not (cron := task_data.labels.get("cron")):
                    logger.warning(f"Cron task missing `cron` label: '{task_data.name}'")
                    continue

                if pycron.is_now(cron, dt=current_time):
                    self._runtime_task_group.create_task(self.run_task(task_data, current_time))

            # NOTE: TaskGroup waits for all tasks to complete before continuing

    @abstractmethod
    async def _block_task(self, task_data: TaskData) -> None:
        """
        Set up a task block_handler task
        """

    @abstractmethod
    async def _event_task(self, task_data: TaskData) -> None:
        """
        Set up a task for the given contract event
        """

    async def startup(self) -> list[Coroutine]:
        """
        Execute runner startup sequence to configure the runner for runtime.

        NOTE: Execution will abort if startup sequence has a failure.

        Returns:
            user_tasks (list[Coroutine]): functions to execute as user daemon tasks
        """

        # Initialize broker (run worker startup events)
        await self.bot.broker.startup()

        # Obtain system configuration for worker
        config = await self.run_system_task(TaskType.SYSTEM_CONFIG)
        logger.info(f"Worker using Silverback SDK v{config.sdk_version}")

        # NOTE: Increase the specifier set here if there is a breaking change to this
        # TODO: set to next breaking change release before release
        if Version(config.sdk_version) not in SpecifierSet(">=0.5.0"):
            raise StartupFailure("Worker SDK version too old, please rebuild")

        supported_task_types = set(TaskType(task_name) for task_name in config.task_types)

        # NOTE: Bypass snapshotting if unsupported
        self._snapshotting_supported = TaskType.SYSTEM_CREATE_SNAPSHOT in supported_task_types

        # Load the snapshot (if available)
        # NOTE: Add some additional handling to see if this feature is available in bot
        if TaskType.SYSTEM_LOAD_SNAPSHOT not in supported_task_types:
            raise StartupFailure(
                "Silverback no longer supports runner-based snapshotting, "
                "please upgrade your bot SDK version to latest to use snapshots."
            )

        elif not (startup_state := await self.datastore.init(self.bot.identifier)):
            logger.warning("No state snapshot detected, using empty snapshot")
            startup_state = StateSnapshot(
                # TODO: Migrate these to parameters (remove explicitly from state)
                last_block_seen=-1,
                last_block_processed=-1,
            )  # Use empty snapshot

        logger.debug(f"Startup state: {startup_state}")
        # NOTE: State snapshot is immediately out of date after init

        # Send startup state to bot
        await self.run_system_task(TaskType.SYSTEM_LOAD_SNAPSHOT, startup_state)

        # NOTE: Do this for other system tasks because they may not be in older SDK versions
        #       `if TaskType.<SYSTEM_TASK_NAME> not in system_tasks: raise StartupFailure(...)`
        #       or handle accordingly by having default logic if it is not available

        # Initialize recorder (if available)
        if self.recorder:
            await self.recorder.init(self.bot.identifier)

        # Execute Silverback startup tasks before we enter into runtime
        if startup_tasks_taskdata := await self.run_system_task(
            TaskType.SYSTEM_USER_TASKDATA, TaskType.STARTUP
        ):
            exceptions_or_none = await quattro.gather(
                *map(lambda td: self.run_task(td, startup_state), startup_tasks_taskdata),
                # NOTE: Any propagated failure in here should be handled so shutdown tasks run
                return_exceptions=True,
            )

            if errors := list(filter(lambda e: e is not None, exceptions_or_none)):
                # NOTE: Abort before even starting to run
                raise StartupFailure(*errors)

            # NOTE: No need to handle results otherwise

        # Create our long-running event listeners
        cron_tasks_taskdata = (
            await self.run_system_task(TaskType.SYSTEM_USER_TASKDATA, TaskType.CRON_JOB)
            if Version(config.sdk_version) >= Version("0.7.15")
            # NOTE: Not supported in prior versions
            else []
        )

        new_block_tasks_taskdata = await self.run_system_task(
            TaskType.SYSTEM_USER_TASKDATA, TaskType.NEW_BLOCK
        )

        event_log_tasks_taskdata = await self.run_system_task(
            TaskType.SYSTEM_USER_TASKDATA, TaskType.EVENT_LOG
        )

        if len(new_block_tasks_taskdata) == len(event_log_tasks_taskdata) == 0:
            raise NoTasksAvailableError()

        return [
            self._cron_tasks(cron_tasks_taskdata),
            *map(self._block_task, new_block_tasks_taskdata),
            *map(self._event_task, event_log_tasks_taskdata),
        ]

    def _cleanup_tasks(self) -> list[Coroutine]:
        return []

    async def shutdown(self):
        """
        Execute the runner shutdown sequence, including user tasks.

        NOTE: Must be placed into runtime before called.
        """

        # Execute all shutdown task(s) before shutting down the broker and bot
        try:
            shutdown_tasks_taskdata = await self.run_system_task(
                TaskType.SYSTEM_USER_TASKDATA, TaskType.SHUTDOWN
            )

        except StartupFailure as e:
            logger.error(f"Error when collecting shutdown tasks: {e}")
            # NOTE: Will cause it to skip to last checkpoint
            shutdown_tasks_taskdata = []

        if shutdown_tasks_taskdata:
            exceptions_or_none = await quattro.gather(
                *map(self.run_task, shutdown_tasks_taskdata),
                # NOTE: Any propagated failure in here should be handled so shutdown tasks run
                return_exceptions=True,
            )

            if errors_str := "\n".join(
                map(str, filter(lambda e: e is not None, exceptions_or_none))
            ):
                # NOTE: Just log errors to avoid exception during shutdown
                logger.error(f"Errors while shutting down:\n{errors_str}")

        # NOTE: Do one last checkpoint to save a snapshot of final state
        if self._snapshotting_supported:
            await self._checkpoint()

        # NOTE: Will trigger worker shutdown function(s)
        await self.bot.broker.shutdown()

        # NOTE: Finally execute runner cleanup tasks
        await quattro.gather(*self._cleanup_tasks())

    def _daemon_tasks(self) -> list[Coroutine]:
        return []

    async def run(self):
        """
        Run the task broker client for the assembled ``SilverbackBot`` bot.

        Will listen for events against the connected provider (using `ManagerAccessMixin` context),
        and process them by kicking events over to the configured broker.

        Raises:
            :class:`~silverback.exceptions.StartupFailure`:
                If there was an exception during startup.
            :class:`~silverback.exceptions.NoTasksAvailableError`:
                If there are no configured tasks to execute.
        """

        # NOTE: No need to display startup text, obvious from loading settings
        user_tasks = await self.startup()

        # NOTE: After startup, we need to gracefully shutdown
        self.shutdown_event = asyncio.Event()

        def exit_handler(signum, _frame):
            logger.info(f"{signal.Signals(signum).name} signal received")
            self.shutdown_event.set()

        # Make sure we handle various ways that OS might kill process
        signal.signal(signal.SIGTERM, exit_handler)
        # NOTE: Overwrite Ape's default signal handler (causes issues)
        signal.signal(signal.SIGINT, exit_handler)

        async def wait_for_graceful_shutdown():
            logger.success("Startup complete, transitioning to runtime")
            await self.shutdown_event.wait()
            raise Halt()  # Trigger shutdown process

        try:
            async with quattro.TaskGroup() as tg:
                # NOTE: Our runtime tasks can use this to spawn more tasks
                self._runtime_task_group = tg

                # NOTE: User tasks that should run forever
                for coro in user_tasks:
                    tg.create_task(coro)

                # NOTE: It is assumed if no user tasks, there is a background task
                for coro in self._daemon_tasks():
                    tg.create_task(coro)

                # NOTE: Will wait forever on this task to halt
                tg.create_task(wait_for_graceful_shutdown())

            # NOTE: If any exception raised by non-background tasks, will quit all

        except ExceptionGroup as eg:
            if error_str := "\n".join(str(e) for e in eg.exceptions if not isinstance(e, Halt)):
                logger.error(error_str)

        logger.warning("Shutdown started")
        await self.shutdown()


class WebsocketRunner(BaseRunner, ManagerAccessMixin):
    """
    Run a single bot against a live network using a basic in-memory queue and websockets.
    """

    def __init__(self, bot: SilverbackBot, *args, **kwargs):
        super().__init__(bot, *args, **kwargs)

        # Check for websocket support
        if not (ws_uri := self.chain_manager.provider.ws_uri):
            raise NoWebsocketAvailableError()

        self.ws_uri = ws_uri

    async def _block_task(self, task_data: TaskData):

        async def block_handler(ctx: NewHeadsSubscriptionContext):
            block = self.provider.network.ecosystem.decode_block(dict(ctx.result))
            await self._checkpoint(last_block_seen=block.number)
            await self.run_task(task_data, block)
            await self._checkpoint(last_block_processed=block.number)

        sub_id = await self._web3.subscription_manager.subscribe(
            NewHeadsSubscription(label=task_data.name, handler=block_handler)
        )
        logger.debug(f"Handling blocks via {sub_id}")

    async def _event_task(self, task_data: TaskData):
        if not (contract_address := task_data.labels.get("contract_address")):
            raise StartupFailure("Contract instance required.")

        if not (event_signature := task_data.labels.get("event_signature")):
            raise StartupFailure("No Event Signature provided.")

        event_abi = EventABI.from_signature(event_signature)

        async def log_handler(ctx: LogsSubscriptionContext):
            event = next(  # NOTE: `next` is okay since it only has one item
                self.provider.network.ecosystem.decode_logs([ctx.result], event_abi)
            )
            # TODO: Fix upstream w/ web3py
            event.transaction_hash = "0x" + event.transaction_hash.hex()
            await self._checkpoint(last_block_seen=event.block_number)
            await self.run_task(task_data, event)
            await self._checkpoint(last_block_processed=event.block_number)

        sub_id = await self._web3.subscription_manager.subscribe(
            LogsSubscription(
                label=task_data.name,
                address=contract_address,
                topics=[to_hex(keccak(text=event_abi.selector))],
                handler=log_handler,
            )
        )
        logger.debug(f"Handling '{contract_address}:{event_abi.name}' logs via {sub_id}")

    def _daemon_tasks(self) -> list[Coroutine]:
        # NOTE: Handle this as a daemon task (after startup)
        return [self._web3.subscription_manager.handle_subscriptions(run_forever=True)]

    def _cleanup_tasks(self) -> list[Coroutine]:
        return [self._web3.subscription_manager.unsubscribe_all()]

    async def run(self):
        async with AsyncWeb3(WebSocketProvider(self.ws_uri)) as web3:
            self._web3 = web3
            await super().run()


class PollingRunner(BaseRunner, ManagerAccessMixin):
    """
    Run a single bot against a live network using a basic in-memory queue.
    """

    # TODO: Move block_timeout settings to Ape core config
    # TODO: Merge polling/websocket subscriptions downstream in Ape core

    def __init__(self, bot: SilverbackBot, *args, **kwargs):
        super().__init__(bot, *args, **kwargs)
        logger.warning(
            "The polling runner makes a significant amount of requests. "
            "Do not use in production over long time periods unless you know what you're doing."
        )

    async def _block_task(self, task_data: TaskData):
        if block_settings := self.bot.poll_settings.get("_blocks_"):
            new_block_timeout = block_settings.get("new_block_timeout")
        else:
            new_block_timeout = None

        new_block_timeout = (
            new_block_timeout if new_block_timeout is not None else self.bot.new_block_timeout
        )

        async for block in async_wrap_iter(
            chain.blocks.poll_blocks(
                # NOTE: No start block because we should begin polling from head
                new_block_timeout=new_block_timeout,
            )
        ):
            await self._checkpoint(last_block_seen=block.number)
            await self.run_task(task_data, block)
            await self._checkpoint(last_block_processed=block.number)

    async def _event_task(self, task_data: TaskData):
        if not (contract_address := task_data.labels.get("contract_address")):
            raise StartupFailure("Contract instance required.")

        if not (event_signature := task_data.labels.get("event_signature")):
            raise StartupFailure("No Event Signature provided.")

        event_abi = EventABI.from_signature(event_signature)

        if address_settings := self.bot.poll_settings.get(contract_address):
            new_block_timeout = address_settings.get("new_block_timeout")
        else:
            new_block_timeout = None

        new_block_timeout = (
            new_block_timeout if new_block_timeout is not None else self.bot.new_block_timeout
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
            await self.run_task(task_data, event)
            await self._checkpoint(last_block_processed=event.block_number)
