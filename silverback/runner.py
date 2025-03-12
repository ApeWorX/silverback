import asyncio
import signal
import sys
from abc import ABC, abstractmethod
from typing import Any, Callable

import quattro
from ape import chain
from ape.logging import logger
from ape.utils import ManagerAccessMixin
from ape_ethereum.ecosystem import keccak
from eth_utils import to_hex
from ethpm_types import EventABI
from packaging.specifiers import SpecifierSet
from packaging.version import Version
from taskiq import AsyncTaskiqTask
from taskiq.kicker import AsyncKicker
from web3 import AsyncWeb3, WebSocketProvider
from web3.utils.subscriptions import (
    LogsSubscription,
    LogsSubscriptionContext,
    NewHeadsSubscription,
    NewHeadsSubscriptionContext,
)

from .exceptions import Halt, NoTasksAvailableError, NoWebsocketAvailableError, StartupFailure
from .main import SilverbackBot, TaskData
from .recorder import BaseRecorder, TaskResult
from .state import Datastore, StateSnapshot
from .types import TaskType
from .utils import async_wrap_iter, run_taskiq_task_group_wait_results, run_taskiq_task_wait_result


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

    def _create_task_kicker(self, task_data: TaskData) -> AsyncKicker:
        return AsyncKicker(
            task_name=task_data.name, broker=self.bot.broker, labels=task_data.labels
        )

    def _create_system_task_kicker(self, task_type: TaskType) -> AsyncKicker:
        assert "system:" in str(task_type)
        return self._create_task_kicker(TaskData(name=str(task_type), labels={}))

    async def run_system_task(self, task_type: TaskType, *args) -> Any:
        system_task_kicker = self._create_system_task_kicker(task_type)
        if (result := await run_taskiq_task_wait_result(system_task_kicker, *args)).is_err:
            raise StartupFailure(f"System Task Failure [{task_type.name}]: {result.error}")
        return result.return_value

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
        if not self._snapshotting_supported:
            return  # Can't support this feature

        task = await self.bot._create_snapshot.kiq(last_block_seen, last_block_processed)
        if (result := await task.wait_result()).is_err:
            logger.error(f"Error saving snapshot: {result.error}")
        else:
            await self.datastore.save(result.return_value)

    @abstractmethod
    async def _block_task(self, task_data: TaskData) -> asyncio.Task | None:
        """
        Handle a block_handler task
        """

    @abstractmethod
    async def _event_task(self, task_data: TaskData) -> asyncio.Task | None:
        """
        Handle an event handler task for the given contract event
        """

    async def run(self, *runtime_tasks: asyncio.Task | Callable[[], asyncio.Task]):
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

        def exit_handler(signum, _frame):
            logger.info(f"{signal.Signals(signum).name} signal received")
            sys.exit(0)  # Exit normally

        # NOTE: Make sure we handle various ways that OS might kill process
        signal.signal(signal.SIGTERM, exit_handler)
        # NOTE: Overwrite Ape's default signal handler (causes issues)
        signal.signal(signal.SIGINT, exit_handler)

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

            startup_task_results = await run_taskiq_task_group_wait_results(
                (map(self._create_task_kicker, startup_tasks_taskdata)), startup_state
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
        new_block_tasks_taskdata = await self.run_system_task(
            TaskType.SYSTEM_USER_TASKDATA, TaskType.NEW_BLOCK
        )

        event_log_tasks_taskdata = await self.run_system_task(
            TaskType.SYSTEM_USER_TASKDATA, TaskType.EVENT_LOG
        )

        if len(new_block_tasks_taskdata) == len(event_log_tasks_taskdata) == 0:
            raise NoTasksAvailableError()

        try:
            # NOTE: Block any interrupts during runtime w/ `asyncio.shield` to shutdown gracefully
            exceptions_or_none = await asyncio.shield(
                quattro.gather(
                    # NOTE: `_block_task`/`_event_task` either never complete (daemon task) or
                    #       immediately return None. In the case they return None, it is expected
                    #       that `runtime_tasks` contain at least one daemon task so that
                    #       `quattro.gather` does not return.
                    *map(self._block_task, new_block_tasks_taskdata),
                    *map(self._event_task, event_log_tasks_taskdata),
                    *(t if isinstance(t, asyncio.Task) else t() for t in runtime_tasks),
                    # NOTE: Any propagated failure in here should be handled so shutdown tasks run
                    return_exceptions=True,
                )
            )

        except asyncio.CancelledError:
            # NOTE: Use this to continue with shutdown if interrupted
            exceptions_or_none = (None,)

        # NOTE: `quattro.gather` runs until one task bubbles up an exception that stops execution
        if runtime_errors := "\n".join(str(e) for e in exceptions_or_none if e is not None):
            # NOTE: In case we are somehow not displaying the error correctly with task status
            logger.warning(f"Runtime error(s) detected, shutting down:\n{runtime_errors}")

        # Execute all shutdown task(s) before shutting down the broker and bot
        try:
            shutdown_tasks_taskdata = await self.run_system_task(
                TaskType.SYSTEM_USER_TASKDATA, TaskType.SHUTDOWN
            )

        except StartupFailure:
            logger.error("Error when collecting shutdown tasks")
            # NOTE: Will cause it to skip to last checkpoint
            shutdown_tasks_taskdata = []

        if shutdown_tasks_taskdata:
            shutdown_task_results = await run_taskiq_task_group_wait_results(
                map(self._create_task_kicker, shutdown_tasks_taskdata)
            )

            if errors_str := "\n".join(
                str(result.error) for result in shutdown_task_results if result.is_err
            ):
                # NOTE: Just log errors to avoid exception during shutdown
                logger.error(f"Errors while shutting down:\n{errors_str}")

            # NOTE: Run recorder for all shutdown tasks regardless of status
            if self.recorder:
                converted_results = map(TaskResult.from_taskiq, shutdown_task_results)
                await asyncio.gather(*(self.recorder.add_result(r) for r in converted_results))

        # NOTE: Do one last checkpoint to save a snapshot of final state
        if self._snapshotting_supported:
            await self._checkpoint()

        # NOTE: Will trigger worker shutdown function(s)
        await self.bot.broker.shutdown()


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

    async def _block_task(self, task_data: TaskData) -> None:
        new_block_task_kicker = self._create_task_kicker(task_data)

        async def block_handler(ctx: NewHeadsSubscriptionContext):
            block = self.provider.network.ecosystem.decode_block(dict(ctx.result))
            await self._checkpoint(last_block_seen=block.number)
            await self._handle_task(await new_block_task_kicker.kiq(block))
            await self._checkpoint(last_block_processed=block.number)

        sub_id = await self._web3.subscription_manager.subscribe(
            NewHeadsSubscription(label=task_data.name, handler=block_handler)
        )
        logger.debug(f"Handling blocks via {sub_id}")

    async def _event_task(self, task_data: TaskData) -> None:
        if not (contract_address := task_data.labels.get("contract_address")):
            raise StartupFailure("Contract instance required.")

        if not (event_signature := task_data.labels.get("event_signature")):
            raise StartupFailure("No Event Signature provided.")

        event_abi = EventABI.from_signature(event_signature)

        event_log_task_kicker = self._create_task_kicker(task_data)

        async def log_handler(ctx: LogsSubscriptionContext):
            event = next(  # NOTE: `next` is okay since it only has one item
                self.provider.network.ecosystem.decode_logs([ctx.result], event_abi)
            )
            # TODO: Fix upstream w/ web3py
            event.transaction_hash = "0x" + event.transaction_hash.hex()
            await self._checkpoint(last_block_seen=event.block_number)
            await self._handle_task(await event_log_task_kicker.kiq(event))
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

    async def run(self, *runtime_tasks: asyncio.Task | Callable[[], asyncio.Task]):
        async with AsyncWeb3(WebSocketProvider(self.ws_uri)) as web3:
            self._web3 = web3

            def run_subscriptions() -> asyncio.Task:
                return asyncio.create_task(
                    web3.subscription_manager.handle_subscriptions(run_forever=True)
                )

            # NOTE: This triggers daemon tasks
            await super().run(*runtime_tasks, run_subscriptions)
            try:
                # TODO: ctrl+C raises `websockets.exceptions.ConnectionClosedError`
                await web3.subscription_manager.unsubscribe_all()
            except Exception as e:
                # NOTE: We don't really need to see any errors from here
                logger.debug(str(e))


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

    async def _block_task(self, task_data: TaskData) -> asyncio.Task:
        new_block_task_kicker = self._create_task_kicker(task_data)

        if block_settings := self.bot.poll_settings.get("_blocks_"):
            new_block_timeout = block_settings.get("new_block_timeout")
        else:
            new_block_timeout = None

        new_block_timeout = (
            new_block_timeout if new_block_timeout is not None else self.bot.new_block_timeout
        )

        async def block_handler():
            async for block in async_wrap_iter(
                chain.blocks.poll_blocks(
                    # NOTE: No start block because we should begin polling from head
                    new_block_timeout=new_block_timeout,
                )
            ):
                await self._checkpoint(last_block_seen=block.number)
                await self._handle_task(await new_block_task_kicker.kiq(block))
                await self._checkpoint(last_block_processed=block.number)

        return asyncio.create_task(block_handler())

    async def _event_task(self, task_data: TaskData) -> asyncio.Task:
        if not (contract_address := task_data.labels.get("contract_address")):
            raise StartupFailure("Contract instance required.")

        if not (event_signature := task_data.labels.get("event_signature")):
            raise StartupFailure("No Event Signature provided.")

        event_abi = EventABI.from_signature(event_signature)

        event_log_task_kicker = self._create_task_kicker(task_data)
        if address_settings := self.bot.poll_settings.get(contract_address):
            new_block_timeout = address_settings.get("new_block_timeout")
        else:
            new_block_timeout = None

        new_block_timeout = (
            new_block_timeout if new_block_timeout is not None else self.bot.new_block_timeout
        )

        async def log_handler():
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

        return asyncio.create_task(log_handler())
