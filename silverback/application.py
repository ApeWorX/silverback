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
from .state import AppDatastore, StateSnapshot
from .types import ParamChangeDatapoint, SilverbackID, TaskType, is_scalar_type


class SystemConfig(BaseModel):
    # NOTE: Do not change this datatype unless major breaking

    # NOTE: Useful for determining if Runner can handle this app
    sdk_version: str
    # NOTE: Useful for specifying what task types can be specified by app
    task_types: list[str]


class TaskData(BaseModel):
    # NOTE: Data we need to know how to call a task via kicker
    name: str  # Name of user function
    labels: dict[str, Any]

    # NOTE: Any other items here must have a default value


class ParameterInfo(BaseModel):
    default: Any

    # NOTE: Any other items here must have a default value


class SharedState(defaultdict):
    def __init__(self):
        # Any unknown key returns None
        super().__init__(lambda: None)

    def __getattr__(self, attr):
        try:
            super().__getattr__(attr)
        except AttributeError:
            return super().__getitem__(attr)

    def __setattr__(self, attr, val):
        try:
            super().__setattr__(attr, val)
        except AttributeError:
            super().__setitem__(attr, val)


class SilverbackApp(ManagerAccessMixin):
    """
    The application singleton. Must be initialized prior to use.

    Usage example::

        from silverback import SilverbackApp

        app = SilverbackApp()

        ...  # Connection has been initialized, can call broker methods e.g. `app.on_(...)`
    """

    def __init__(self, settings: Settings | None = None):
        """
        Create app

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
            name=settings.APP_NAME,
            network=provider.network.name,
            ecosystem=provider.network.ecosystem.name,
        )

        # Adjust defaults from connection
        if settings.NEW_BLOCK_TIMEOUT is None and (
            provider.network.name.endswith("-fork") or provider.network.name == LOCAL_NETWORK_NAME
        ):
            settings.NEW_BLOCK_TIMEOUT = int(timedelta(days=1).total_seconds())

        settings_str = "\n  ".join(f'{key}="{val}"' for key, val in settings.dict().items() if val)
        logger.info(f"Loading Silverback App with settings:\n  {settings_str}")

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
        self.start_block = settings.START_BLOCK

        signer_str = f"\n  SIGNER={repr(self.signer)}"
        start_block_str = f"\n  START_BLOCK={self.start_block}" if self.start_block else ""
        new_block_timeout_str = (
            f"\n  NEW_BLOCK_TIMEOUT={self.new_block_timeout}" if self.new_block_timeout else ""
        )

        network_choice = f"{self.identifier.ecosystem}:{self.identifier.network}"
        logger.success(
            f'Loaded Silverback App:\n  NETWORK="{network_choice}"'
            f"{signer_str}{start_block_str}{new_block_timeout_str}"
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

        # TODO: Make backup optional and settings-driven
        # TODO: Allow configuring backup class
        self.datastore = AppDatastore()
        self._load_snapshot = self.__register_system_task(
            TaskType.SYSTEM_LOAD_SNAPSHOT, self.__load_snapshot_handler
        )
        self._save_snapshot = self.__register_system_task(
            TaskType.SYSTEM_SAVE_SNAPSHOT, self.__save_snapshot_handler
        )

        # NOTE: The runner needs to know the set of things that the app is tracking as a parameter
        # NOTE: We also need to know the defaults in case the parameters are not in the snapshot
        self.__parameters: dict[str, ParameterInfo] = {
            # System state parameters
            "system:last_block_seen": ParameterInfo(default=-1),
            "system:last_block_processed": ParameterInfo(default=-1),
        }
        self._batch_set_param = self.__register_system_task(
            TaskType.SYSTEM_SET_PARAM_BATCH, self.__batch_param_set_handler
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

    async def __load_snapshot_handler(self) -> StateSnapshot:
        # NOTE: This is not networked in any way, nor thread-safe nor multi-process safe,
        #       but will be accessible across multiple workers in a single container
        # NOTE: *DO NOT USE* in Runner, as it will not be updated by the app
        self.state = SharedState()
        # NOTE: attribute does not exist before this task is executed,
        #       ensuring no one uses it during worker startup

        if not (startup_state := await self.datastore.init(app_id=self.identifier)):
            logger.warning("No state snapshot detected, using empty snapshot")
            # TODO: Refactor to `None` by removing
            self.state["system:last_block_seen"] = -1
            self.state["system:last_block_processed"] = -1
            startup_state = StateSnapshot(
                # TODO: Migrate these to parameters (remove explicitly from state)
                last_block_seen=-1,
                last_block_processed=-1,
            )  # Use empty snapshot

        for param_name, param_info in self.parameters.items():

            if (cached_value := startup_state.parameters.get(param_name)) is not None:
                logger.info(f"Found cached value for app.state['{param_name}']: {cached_value}")
                self.state[param_name] = cached_value

            elif param_info.default is not None:
                logger.info(
                    f"Cached value not found for app.state['{param_name}']"
                    f", using default: {param_info.default}"
                )
                self.state[param_name] = param_info.default

            # NOTE: `None` default doesn't need to be set because that's how SharedState works

        return startup_state

    async def __save_snapshot_handler(
        self,
        last_block_seen: int | None = None,
        last_block_processed: int | None = None,
    ):
        # Task that backups state before/after every non-system runtime task and at shutdown
        if last_block_seen is not None:
            self.state["system:last_block_seen"] = last_block_seen

        if last_block_processed is not None:
            self.state["system:last_block_processed"] = last_block_processed

        snapshot = StateSnapshot(
            # TODO: Migrate these to parameters (remove explicitly from state)
            last_block_processed=self.state["system:last_block_seen"] or -1,
            last_block_seen=self.state["system:last_block_processed"] or -1,
            parameters={param_name: self.state[param_name] for param_name in self.parameters},
        )

        return await self.datastore.save(snapshot)

    async def __batch_param_set_handler(
        self, parameter_updates: dict
    ) -> dict[str, ParamChangeDatapoint]:
        datapoints = {}
        for param_name, new_value in parameter_updates.items():
            if "system:" in param_name:
                logger.error(f"Cannot update system parameter '{param_name}'")

            elif param_name not in self.parameters:
                logger.error(f"Unrecognized parameter '{param_name}'")

            else:
                datapoints[param_name] = ParamChangeDatapoint(
                    old=self.state[param_name], new=new_value
                )
                logger.success(f"Update: app.state['{param_name}'] = {new_value}")
                self.state[param_name] = new_value

        # NOTE: This is one blocking atomic task, it must be handled atomically
        await self.datastore.save(self._create_snapshot())
        return datapoints

    @property
    def parameters(self) -> dict[str, ParameterInfo]:
        # NOTE: makes this variable read-only
        return self.__parameters

    def add_parameter(self, param_name: str, default: Any = None):
        if "system:" in param_name:
            raise ValueError("Cannot override system parameters")

        if param_name in self.parameters:
            raise ValueError(f"{param_name} already added!")

        if default and not is_scalar_type(default):
            raise ValueError(f"Default value type '{type(default)}' is not a valid scalar type.")

        # Update this to track parameter existance/default value/update handler
        self.__parameters[param_name] = ParameterInfo(default=default)

    def broker_task_decorator(
        self,
        task_type: TaskType,
        container: BlockContainer | ContractEvent | None = None,
    ) -> Callable[[Callable], AsyncTaskiqDecoratedTask]:
        """
        Dynamically create a new broker task that handles tasks of ``task_type``.

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
        Code to execute on one worker upon startup / restart after an error.

        Usage example::

            @app.on_startup()
            def do_something_on_startup(startup_state):
                ...  # Reprocess missed events or blocks
        """
        return self.broker_task_decorator(TaskType.STARTUP)

    def on_shutdown(self) -> Callable:
        """
        Code to execute on one worker at shutdown.

        Usage example::

            @app.on_shutdown()
            def do_something_on_shutdown():
                ...  # Record final state of app
        """
        return self.broker_task_decorator(TaskType.SHUTDOWN)

    def on_worker_startup(self) -> Callable:
        """
        Code to execute on every worker at startup / restart after an error.

        Usage example::

            @app.on_startup()
            def do_something_on_startup(state):
                ...  # Can provision resources, or add things to `state`.
        """
        return self.broker.on_event(TaskiqEvents.WORKER_STARTUP)

    def on_worker_shutdown(self) -> Callable:
        """
        Code to execute on every worker at shutdown.

        Usage example::

            @app.on_shutdown()
            def do_something_on_shutdown(state):
                ...  # Update some external service, perhaps using information from `state`.
        """
        return self.broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)

    def on_(
        self,
        container: BlockContainer | ContractEvent,
        new_block_timeout: int | None = None,
        start_block: int | None = None,
    ):
        """
        Create task to handle events created by `container`.

        Args:
            container: (BlockContainer | ContractEvent): The event source to watch.
            new_block_timeout: (int | None): Override for block timeout that is acceptable.
                Defaults to whatever the app's settings are for default polling timeout are.
            start_block (int | None): block number to start processing events from.
                Defaults to whatever the latest block is.

        Raises:
            :class:`~silverback.exceptions.InvalidContainerTypeError`:
                If the type of `container` is not configurable for the app.
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
        # TODO: Support mempool polling
        raise InvalidContainerTypeError(container)
