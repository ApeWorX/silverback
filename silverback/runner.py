import asyncio
from abc import ABC, abstractmethod
from typing import Callable

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
from .main import SilverbackBot, SystemConfig, TaskData
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
            task_name=f"{self.bot.identifier.name}:{task_data.name}",
            broker=self.bot.broker,
            labels=task_data.labels,
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
        # Initialize broker (run worker startup events)
        await self.bot.broker.startup()

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

        # NOTE: Bypass snapshotting if unsupported
        self._snapshotting_supported = TaskType.SYSTEM_CREATE_SNAPSHOT in system_tasks

        # Load the snapshot (if available)
        # NOTE: Add some additional handling to see if this feature is available in bot
        if TaskType.SYSTEM_LOAD_SNAPSHOT not in system_tasks:
            logger.warning(
                "Silverback no longer supports runner-based snapshotting, "
                "please upgrade your bot SDK version to latest to use snapshots."
            )
            startup_state: StateSnapshot | None = StateSnapshot(
                last_block_seen=-1,
                last_block_processed=-1,
            )  # Use empty snapshot

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
        if (
            result := await run_taskiq_task_wait_result(
                self._create_system_task_kicker(TaskType.SYSTEM_LOAD_SNAPSHOT), startup_state
            )
        ).is_err:
            raise StartupFailure(result.error)

        # NOTE: Do this for other system tasks because they may not be in older SDK versions
        #       `if TaskType.<SYSTEM_TASK_NAME> not in system_tasks: raise StartupFailure(...)`
        #       or handle accordingly by having default logic if it is not available

        # Initialize recorder (if available)
        if self.recorder:
            await self.recorder.init(self.bot.identifier)

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
                (task_handler for task_handler in startup_task_handlers), startup_state
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
        listener_tasks = []
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
        for task_def in new_block_taskdata_results.return_value:
            if (task := await self._block_task(task_def)) is not None:
                listener_tasks.append(task)

        for task_def in event_log_taskdata_results.return_value:
            if (task := await self._event_task(task_def)) is not None:
                listener_tasks.append(task)

        listener_tasks.extend(t if isinstance(t, asyncio.Task) else t() for t in runtime_tasks)

        # NOTE: Safe to do this because no tasks were actually scheduled to run
        if len(listener_tasks) == 0:
            raise NoTasksAvailableError()

        # Run until one task bubbles up an exception that should stop execution
        # TODO: `asyncio.TaskGroup` added in Python 3.11
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

        # Execute Silverback shutdown task(s) before shutting down the broker and bot
        shutdown_taskdata_result = await run_taskiq_task_wait_result(
            self._create_system_task_kicker(TaskType.SYSTEM_USER_TASKDATA), TaskType.SHUTDOWN
        )

        if shutdown_taskdata_result.is_err:
            logger.error(f"Error when collecting shutdown tasks:\n{shutdown_taskdata_result.error}")

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

        if self._snapshotting_supported:
            # Do one last checkpoint to save a snapshot of final state
            await self._checkpoint()

        await self.bot.broker.shutdown()  # Release broker


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

            await super().run(*runtime_tasks, run_subscriptions)
            await web3.subscription_manager.unsubscribe_all()


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
