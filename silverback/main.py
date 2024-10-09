import atexit
from collections import defaultdict
from datetime import timedelta
from typing import Any, Callable

from ape.api.networks import LOCAL_NETWORK_NAME
from ape.contracts import ContractEvent, ContractInstance
from ape.logging import logger
from ape.managers.chain import BlockContainer
from ape.utils import ManagerAccessMixin
from packaging.version import Version
from pydantic import BaseModel
from taskiq import AsyncTaskiqDecoratedTask, TaskiqEvents

from .exceptions import ContainerTypeMismatchError, InvalidContainerTypeError
from .settings import Settings
from .state import StateSnapshot
from .types import SilverbackID, TaskType


class SystemConfig(BaseModel):
    # NOTE: Do not change this datatype unless major breaking

    # NOTE: Useful for determining if Runner can handle this bot
    sdk_version: str
    # NOTE: Useful for specifying what task types can be specified by bot
    task_types: list[str]


class TaskData(BaseModel):
    # NOTE: Data we need to know how to call a task via kicker
    name: str  # Name of user function
    labels: dict[str, Any]

    # NOTE: Any other items here must have a default value


class SharedState(defaultdict):
    """
    Class containing the bot shared state that all workers can read from and write to.

    ```{warning}
    This is not networked in any way, nor is it multi-process safe, but will be
    accessible across multiple thread workers within a single process.
    ```

    Usage example::

        @bot.on_(...)
        def do_something_with_state(value):
            # Read from state using `getattr`
            ... = bot.state.something

            # Set state using `setattr`
            bot.state.something = ...

            # Read from state using `getitem`
            ... = bot.state["something"]

            # Set state using setitem
            bot.state["something"] = ...
    """

    # TODO: This class does not have thread-safe access control, but should remain safe due to
    #       it being a memory mapping, and writes are strictly controlled to be handled only by
    #       one worker at a time. There may be issues with using this in production however.

    def __init__(self):
        # Any unknown key returns None
        super().__init__(lambda: None)

    def __getattr__(self, attr):
        try:
            return super().__getattr__(attr)
        except AttributeError:
            return super().__getitem__(attr)

    def __setattr__(self, attr, val):
        try:
            super().__setattr__(attr, val)
        except AttributeError:
            super().__setitem__(attr, val)


class SilverbackBot(ManagerAccessMixin):
    """
    The bot singleton. Must be initialized prior to use.

    Usage example::

        from silverback import SilverbackBot

        bot = SilverbackBot()

        ...  # Connection has been initialized, can call broker methods e.g. `bot.on_(...)`
    """

    def __init__(self, settings: Settings | None = None):
        """
        Create bot

        Args:
            settings (~:class:`silverback.settings.Settings` | None): Settings override.
                Defaults to environment settings.
        """
        if not settings:
            settings = Settings()

        provider_context = settings.get_provider_context()
        # NOTE: This allows using connected ape methods e.g. `Contract`
        provider = provider_context.__enter__()

        self.identifier = SilverbackID(
            name=settings.BOT_NAME,
            network=provider.network.name,
            ecosystem=provider.network.ecosystem.name,
        )

        # Adjust defaults from connection
        if settings.NEW_BLOCK_TIMEOUT is None and (
            provider.network.name.endswith("-fork") or provider.network.name == LOCAL_NETWORK_NAME
        ):
            settings.NEW_BLOCK_TIMEOUT = int(timedelta(days=1).total_seconds())

        settings_str = "\n  ".join(f'{key}="{val}"' for key, val in settings.dict().items() if val)
        logger.info(f"Loading Silverback Bot with settings:\n  {settings_str}")

        self.broker = settings.get_broker()
        self.tasks: dict[TaskType, list[TaskData]] = {
            task_type: []
            for task_type in TaskType
            # NOTE: Dont track system tasks
            if not str(task_type).startswith("system:")
        }
        self.poll_settings: dict[str, dict] = {}

        atexit.register(provider_context.__exit__, None, None, None)

        self.signer = settings.get_signer()
        self.new_block_timeout = settings.NEW_BLOCK_TIMEOUT

        signer_str = f"\n  SIGNER={repr(self.signer)}"
        new_block_timeout_str = (
            f"\n  NEW_BLOCK_TIMEOUT={self.new_block_timeout}" if self.new_block_timeout else ""
        )

        network_choice = f"{self.identifier.ecosystem}:{self.identifier.network}"
        logger.success(
            f'Loaded Silverback Bot:\n  NETWORK="{network_choice}"'
            f"{signer_str}{new_block_timeout_str}"
        )

        # NOTE: Runner must call this to configure itself for all SDK hooks
        self._get_system_config = self.__register_system_task(
            TaskType.SYSTEM_CONFIG, self.__get_system_config_handler
        )
        # NOTE: Register other system tasks here
        self._get_user_taskdata = self.__register_system_task(
            TaskType.SYSTEM_USER_TASKDATA, self.__get_user_taskdata_handler
        )
        self._get_user_all_taskdata = self.__register_system_task(
            TaskType.SYSTEM_USER_ALL_TASKDATA, self.__get_user_all_taskdata_handler
        )
        self._load_snapshot = self.__register_system_task(
            TaskType.SYSTEM_LOAD_SNAPSHOT, self.__load_snapshot_handler
        )
        self._create_snapshot = self.__register_system_task(
            TaskType.SYSTEM_CREATE_SNAPSHOT, self.__create_snapshot_handler
        )

    def __register_system_task(
        self, task_type: TaskType, task_handler: Callable
    ) -> AsyncTaskiqDecoratedTask:
        assert str(task_type).startswith("system:"), "Can only add system tasks"
        # NOTE: This has to be registered with the broker in the worker
        return self.broker.register_task(
            task_handler,
            # NOTE: Name makes it impossible to conflict with user's handler fn names
            task_name=str(task_type),
            task_type=str(task_type),
        )

    def __get_system_config_handler(self) -> SystemConfig:
        # NOTE: This is actually executed on the worker side
        from silverback.version import __version__

        return SystemConfig(
            sdk_version=Version(__version__).base_version,
            task_types=[str(t) for t in TaskType],
        )

    def __get_user_taskdata_handler(self, task_type: TaskType) -> list[TaskData]:
        # NOTE: This is actually executed on the worker side
        assert str(task_type).startswith("user:"), "Can only fetch user task data"
        return self.tasks.get(task_type, [])

    def __get_user_all_taskdata_handler(self) -> list[TaskData]:
        return [v for k, l in self.tasks.items() if str(k).startswith("user:") for v in l]

    async def __load_snapshot_handler(self, startup_state: StateSnapshot):
        # NOTE: *DO NOT USE* in Runner, as it will not be updated by the bot
        self.state = SharedState()
        # NOTE: attribute does not exist before this task is executed,
        #       ensuring no one uses it during worker startup

        self.state["system:last_block_seen"] = startup_state.last_block_seen
        self.state["system:last_block_processed"] = startup_state.last_block_processed
        # TODO: Load user custom state (should not start with `system:`)

    async def __create_snapshot_handler(
        self,
        last_block_seen: int | None = None,
        last_block_processed: int | None = None,
    ):
        # Task that updates state checkpoints before/after every non-system runtime task/at shutdown
        if last_block_seen is not None:
            self.state["system:last_block_seen"] = last_block_seen

        if last_block_processed is not None:
            self.state["system:last_block_processed"] = last_block_processed

        return StateSnapshot(
            # TODO: Migrate these to parameters (remove explicitly from state)
            last_block_seen=self.state.get("system:last_block_seen", -1),
            last_block_processed=self.state.get("system:last_block_processed", -1),
        )

    def broker_task_decorator(
        self,
        task_type: TaskType,
        container: BlockContainer | ContractEvent | None = None,
    ) -> Callable[[Callable], AsyncTaskiqDecoratedTask]:
        """
        Dynamically create a new broker task that handles tasks of ``task_type``.

        ```{warning}
        Dynamically creating a task does not ensure that the runner will be aware of the task
        in order to trigger it. Use at your own risk.
        ```

        Args:
            task_type: :class:`~silverback.types.TaskType`: The type of task to create.
            container: (BlockContainer | ContractEvent): The event source to watch.

        Returns:
            Callable[[Callable], :class:`~taskiq.AsyncTaskiqDecoratedTask`]:
                A function wrapper that will register the task handler.

        Raises:
            :class:`~silverback.exceptions.ContainerTypeMismatchError`:
                If there is a mismatch between `task_type` and the `container`
                type it should handle.
        """
        if (
            (task_type is TaskType.NEW_BLOCK and not isinstance(container, BlockContainer))
            or (task_type is TaskType.EVENT_LOG and not isinstance(container, ContractEvent))
            or (
                task_type
                not in (
                    TaskType.NEW_BLOCK,
                    TaskType.EVENT_LOG,
                )
                and container is not None
            )
        ):
            raise ContainerTypeMismatchError(task_type, container)

        # Register user function as task handler with our broker
        def add_taskiq_task(handler: Callable) -> AsyncTaskiqDecoratedTask:
            labels = {"task_type": str(task_type)}

            # NOTE: Do *not* do `if container` because that does a `len(container)` call,
            #       which for ContractEvent queries *every single log* ever emitted, and really
            #       we only want to determine if it is not None
            if container is not None and isinstance(container, ContractEvent):
                # Address is almost a certainty if the container is being used as a filter here.
                if not (contract_address := getattr(container.contract, "address", None)):
                    raise InvalidContainerTypeError(
                        "Please provider a contract event from a valid contract instance."
                    )

                labels["contract_address"] = contract_address
                labels["event_signature"] = container.abi.signature

            self.tasks[task_type].append(TaskData(name=handler.__name__, labels=labels))

            return self.broker.register_task(
                handler,
                task_name=handler.__name__,
                **labels,
            )

        return add_taskiq_task

    def on_startup(self) -> Callable:
        """
        Code that will be exected by one worker after worker startup, but before the
        bot is put into the "run" state by the Runner.

        Usage example::

            @bot.on_startup()
            def do_something_on_startup(startup_state: StateSnapshot):
                ...  # Reprocess missed events or blocks
        """
        return self.broker_task_decorator(TaskType.STARTUP)

    def on_shutdown(self) -> Callable:
        """
        Code that will be exected by one worker before worker shutdown, after the
        Runner has decided to put the bot into the "shutdown" state.

        Usage example::

            @bot.on_shutdown()
            def do_something_on_shutdown():
                ...  # Record final state of bot
        """
        return self.broker_task_decorator(TaskType.SHUTDOWN)

    # TODO: Abstract away worker startup into dependency system
    def on_worker_startup(self) -> Callable:
        """
        Code to execute on every worker immediately after broker startup.

        ```{note}
        This is a great place to load heavy dependencies for the workers,
        such as database connections, ML models, etc.
        ```

        Usage example::

            @bot.on_worker_startup()
            def do_something_on_startup(state):
                ...  # Can provision resources, or add things to `state`.
        """
        return self.broker.on_event(TaskiqEvents.WORKER_STARTUP)

    # TODO: Abstract away worker shutdown into dependency system
    def on_worker_shutdown(self) -> Callable:
        """
        Code to execute on every worker immediately before broker shutdown.

        ```{note}
        This is where you should also release any resources you have loaded during
        worker startup.
        ```

        Usage example::

            @bot.on_worker_shutdown()
            def do_something_on_shutdown(state):
                ...  # Update some external service, perhaps using information from `state`.
        """
        return self.broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)

    def on_(
        self,
        container: BlockContainer | ContractEvent,
        # TODO: possibly remove these
        new_block_timeout: int | None = None,
        start_block: int | None = None,
    ):
        """
        Create task to handle events created by the `container` trigger.

        Args:
            container: (BlockContainer | ContractEvent): The event source to watch.
            new_block_timeout: (int | None): Override for block timeout that is acceptable.
                Defaults to whatever the bot's settings are for default polling timeout are.
            start_block (int | None): block number to start processing events from.
                Defaults to whatever the latest block is.

        Raises:
            :class:`~silverback.exceptions.InvalidContainerTypeError`:
                If the type of `container` is not configurable for the bot.
        """
        if isinstance(container, BlockContainer):
            if new_block_timeout is not None:
                if "_blocks_" in self.poll_settings:
                    self.poll_settings["_blocks_"]["new_block_timeout"] = new_block_timeout
                else:
                    self.poll_settings["_blocks_"] = {"new_block_timeout": new_block_timeout}

            if start_block is not None:
                if "_blocks_" in self.poll_settings:
                    self.poll_settings["_blocks_"]["start_block"] = start_block
                else:
                    self.poll_settings["_blocks_"] = {"start_block": start_block}

            return self.broker_task_decorator(TaskType.NEW_BLOCK, container=container)

        elif isinstance(container, ContractEvent) and isinstance(
            container.contract, ContractInstance
        ):
            key = container.contract.address

            if new_block_timeout is not None:
                if key in self.poll_settings:
                    self.poll_settings[key]["new_block_timeout"] = new_block_timeout
                else:
                    self.poll_settings[key] = {"new_block_timeout": new_block_timeout}

            if start_block is not None:
                if key in self.poll_settings:
                    self.poll_settings[key]["start_block"] = start_block
                else:
                    self.poll_settings[key] = {"start_block": start_block}

            return self.broker_task_decorator(TaskType.EVENT_LOG, container=container)

        # TODO: Support account transaction polling
        # TODO: Support mempool polling?
        raise InvalidContainerTypeError(container)
