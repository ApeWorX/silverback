import atexit
import inspect
from collections import defaultdict
from datetime import datetime, timedelta
from functools import wraps
from types import MethodType
from typing import Any, Awaitable, Callable

import pycron  # type: ignore[import-untyped]
from ape.api.networks import LOCAL_NETWORK_NAME
from ape.contracts import ContractEvent, ContractEventWrapper, ContractInstance
from ape.logging import logger
from ape.managers.chain import BlockContainer
from ape.types import AddressType, ContractLog
from ape.utils import ManagerAccessMixin
from eth_typing import HexStr
from eth_utils import keccak, to_hex
from ethpm_types.abi import encode_topic_value
from packaging.version import Version
from pydantic import BaseModel
from taskiq import AsyncTaskiqDecoratedTask, TaskiqEvents

from .exceptions import ContainerTypeMismatchError, InvalidContainerTypeError, NoSignerLoaded
from .settings import Settings
from .state import StateSnapshot
from .types import SilverbackID, TaskType
from .utils import encode_topics_to_string, parse_hexbytes_dict


class SystemConfig(BaseModel):
    # NOTE: Do not change this datatype unless major breaking

    # NOTE: Useful for determining if Runner can handle this bot
    sdk_version: str
    # NOTE: Useful for specifying what task types can be specified by bot
    task_types: list[str]


class TaskData(BaseModel):
    # NOTE: Data we need to know how to call a task via kicker
    name: str  # Name of user function
    labels: dict[str, str]

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
        self.provider = provider_context.__enter__()

        self.identifier = SilverbackID(
            name=settings.BOT_NAME,
            network=self.provider.network.name,
            ecosystem=self.provider.network.ecosystem.name,
        )

        # Adjust defaults from connection
        if settings.NEW_BLOCK_TIMEOUT is None and (
            self.provider.network.name.endswith("-fork")
            or self.provider.network.name == LOCAL_NETWORK_NAME
        ):
            settings.NEW_BLOCK_TIMEOUT = int(timedelta(days=1).total_seconds())

        settings_str = "\n  ".join(
            f'{key}="{val}"' for key, val in settings.model_dump().items() if val
        )
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
        if self.signer:
            # NOTE: Monkeypatch `AccountAPI.call` to update bot nonce tracking state
            original_call = self.signer.call

            def call_override(account_instance, txn, *args, **kwargs):
                self.state["system:last_nonce_used"] = txn.nonce
                return original_call(txn, *args, **kwargs)

            self.signer.__dict__["call"] = MethodType(call_override, self.signer)

        self.new_block_timeout = settings.NEW_BLOCK_TIMEOUT
        self.use_fork = settings.FORK_MODE and not self.provider.network.name.endswith("-fork")

        signer_str = f"\n  SIGNER={repr(self.signer)}"
        new_block_timeout_str = (
            f"\n  NEW_BLOCK_TIMEOUT={self.new_block_timeout}" if self.new_block_timeout else ""
        )

        network_choice = f"{self.identifier.ecosystem}:{self.identifier.network}"
        logger.success(
            "Loaded Silverback Bot:\n"
            f'  NETWORK="{network_choice}"\n'
            f"  FORK_MODE={self.use_fork}"
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

        # NOTE: We need this as `.register_task` tries to update `.__name__` of `task_handler`,
        #       but methods do not allow setting this attribute (raises AttributeError)
        @wraps(task_handler)
        async def call_task_handler(*args, **kwargs):
            result = task_handler(*args, **kwargs)

            if inspect.isawaitable(result):
                return await result

            return result

        # NOTE: This has to be registered with the broker in the worker
        return self.broker.register_task(
            call_task_handler,
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

        if self.signer:
            # NOTE: 'BaseAddress.nonce` is 1 + last nonce that was used
            self.state["system:last_nonce_used"] = max(
                startup_state.last_nonce_used or -1, self.signer.nonce - 1
            )

        # TODO: Load user custom state (should not start with `system:`)

    async def __create_snapshot_handler(self) -> StateSnapshot:
        return StateSnapshot(
            # TODO: Migrate these to parameters (remove explicitly from state)
            last_block_seen=self.state.get("system:last_block_seen", -1),
            last_block_processed=self.state.get("system:last_block_processed", -1),
            last_nonce_used=self.state.get("system:last_nonce_used"),
        )

    @property
    def nonce(self) -> int:
        if not self.signer:
            raise NoSignerLoaded()

        elif (last_nonce_used := self.state.get("system:last_nonce_used")) is None:
            raise AttributeError(
                "`bot.state` not fully loaded yet, please do not use during worker startup."
            )

        # NOTE: Next nonce (`.nonce` is meant to be used in next txn) is 1 + last
        return max(last_nonce_used + 1, self.signer.nonce)

    def _checkpoint(
        self,
        last_block_seen: int | None = None,
        last_block_processed: int | None = None,
    ):
        # Task that updates state checkpoints before/after every non-system runtime task/at shutdown
        if last_block_seen is not None:
            self.state["system:last_block_seen"] = last_block_seen

        if last_block_processed is not None:
            self.state["system:last_block_processed"] = last_block_processed

    def _ensure_block(self, handler: Callable) -> Callable:
        @wraps(handler)
        async def ensure_block(block, *args, **kwargs):
            # NOTE: With certain runners, `block` may be raw or in it's final form
            if isinstance(block, dict):
                block = self.provider.network.ecosystem.decode_block(parse_hexbytes_dict(block))

            self._checkpoint(last_block_seen=block.number)
            result = handler(block, *args, **kwargs)
            self._checkpoint(last_block_processed=block.number)

            if inspect.isawaitable(result):
                return await result

            return result

        return ensure_block

    def _ensure_log(self, event: ContractEvent, handler: Callable) -> Callable:
        @wraps(handler)
        async def ensure_log(log, *args, **kwargs):
            # NOTE: With certain runners, `log` may be raw or in it's final form
            if isinstance(log, dict):
                if "event_arguments" in log:
                    # This is an Ape object, simply initialize it
                    log = ContractLog(**log)

                else:  # This is a raw web3py object
                    log = next(  # NOTE: `next` is okay since it only has one item
                        self.provider.network.ecosystem.decode_logs(
                            [parse_hexbytes_dict(log)], event.abi
                        )
                    )
                    # TODO: Fix upstream w/ web3py
                    log.transaction_hash = "0x" + log.transaction_hash.hex()

            self._checkpoint(last_block_seen=log.block.number)
            result = handler(log, *args, **kwargs)
            self._checkpoint(last_block_processed=log.block.number)

            if inspect.isawaitable(result):
                return await result

            return result

        # NOTE: Avoid processing w/ TaskIQ's automatic `BaseModel` parser
        ensure_log.__annotations__["log"] = dict
        return ensure_log

    # To ensure we don't have too many forks at once
    # HACK: Until `NetworkManager.fork` (and `ProviderContextManager`) allows concurrency

    def _with_fork_decorator(self, handler: Callable) -> Callable:
        # Trigger worker-side handling using fork network by wrapping handler
        fork_context = self.provider.network_manager.fork

        @wraps(handler)
        async def fork_handler(*args, **kwargs):
            with fork_context():
                result = handler(*args, **kwargs)

                if inspect.isawaitable(result):
                    return await result

                return result

        return fork_handler

    def _convert_arg_to_hexstr(self, arg_value: Any, arg_type: str) -> HexStr | list[HexStr] | None:
        python_type: Any
        if "int" in arg_type:
            python_type = int
        elif "bytes" in arg_type:
            python_type = bytes
        elif arg_type == "address":
            python_type = AddressType
        elif arg_type == "string":
            python_type = str
        else:
            raise ValueError(f"Unable to support ABI Type '{arg_type}'.")

        if isinstance(arg_value, list):
            arg_value = [self.conversion_manager.convert(v, python_type) for v in arg_value]

        else:
            arg_value = self.conversion_manager.convert(arg_value, python_type)

        return encode_topic_value(arg_type, arg_value)  # type: ignore[return-value]

    def broker_task_decorator(
        self,
        task_type: TaskType,
        container: BlockContainer | ContractEvent | ContractEventWrapper | None = None,
        cron_schedule: str | None = None,
        filter_args: dict[str, Any] | None = None,
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
            or (
                task_type is TaskType.EVENT_LOG
                and not isinstance(container, (ContractEvent, ContractEventWrapper))
            )
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

        elif isinstance(container, ContractEventWrapper):
            if len(container.events) != 1:
                raise InvalidContainerTypeError(
                    f"Requires exactly 1 event to unwrap: {container.events}"
                )
            container = container.events[0]

        # Register user function as task handler with our broker
        def add_taskiq_task(
            handler: Callable[..., Any | Awaitable[Any]]
        ) -> AsyncTaskiqDecoratedTask:
            labels: dict[str, str] = dict()

            if task_type is TaskType.NEW_BLOCK:
                handler = self._ensure_block(handler)

            elif task_type is TaskType.EVENT_LOG:
                assert container is not None and isinstance(container, ContractEvent)
                # NOTE: allows broad capture filters (matching multiple addresses)
                if (contract := getattr(container, "contract", None)) and hasattr(
                    contract, "address"
                ):
                    labels["address"] = contract.address

                labels["event"] = container.abi.signature

                topics: list[list[HexStr] | HexStr | None] = [
                    # Topic 0: event_id
                    to_hex(keccak(text=container.abi.selector))
                ]

                # Topic 1-3: event args ([..., ...] represent OR)
                if filter_args:
                    for arg in container.abi.inputs:
                        if not arg.indexed:
                            break  # Inputs should be ordered indexed first

                        if arg_value := filter_args.pop(arg.name, None):
                            topics.append(self._convert_arg_to_hexstr(arg_value, arg.type))

                        else:
                            # Skip this indexed argument (`None` is wildcard match)
                            topics.append(None)
                            # NOTE: Will clean up extra Nones in `encode_topics_to_string`

                    if unmatched_args := "', '".join(filter_args):
                        raise InvalidContainerTypeError(
                            f"Args are not available for filtering: '{unmatched_args}'."
                        )

                labels["topics"] = encode_topics_to_string(topics)

                handler = self._ensure_log(container, handler)

            elif task_type is TaskType.CRON_JOB:
                # NOTE: If cron schedule has never been true over a year timeframe, it's bad
                if not cron_schedule or not pycron.has_been(
                    cron_schedule, datetime.now() - timedelta(days=366)
                ):
                    raise InvalidContainerTypeError(
                        f"'{cron_schedule}' is not a valid cron schedule"
                    )

                labels["cron"] = cron_schedule

            self.tasks[task_type].append(TaskData(name=handler.__name__, labels=labels))

            if self.use_fork:
                handler = self._with_fork_decorator(handler)

            return self.broker.register_task(
                handler,
                task_name=handler.__name__,
                task_type=str(task_type),
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
        filter_args: dict[str, Any] | None = None,
        **filter_kwargs: dict[str, Any],
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

        elif isinstance(container, ContractEvent):
            if isinstance(container.contract, ContractInstance):
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

            if filter_args:
                filter_kwargs.update(filter_args)

            return self.broker_task_decorator(
                TaskType.EVENT_LOG,
                container=container,
                filter_args=filter_kwargs,
            )

        # TODO: Support account transaction polling
        # TODO: Support mempool polling?
        raise InvalidContainerTypeError(container)

    def cron(self, cron_schedule: str) -> Callable:
        """
        Create task to run on a schedule.

        Args:
            cron_schedule (str): A cron-like schedule string.
        """
        return self.broker_task_decorator(TaskType.CRON_JOB, cron_schedule=cron_schedule)
