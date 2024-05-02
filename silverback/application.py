import atexit
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable

from ape.api.networks import LOCAL_NETWORK_NAME
from ape.contracts import ContractEvent, ContractInstance
from ape.logging import logger
from ape.managers.chain import BlockContainer
from ape.utils import ManagerAccessMixin
from taskiq import AsyncTaskiqDecoratedTask, TaskiqEvents

from .exceptions import ContainerTypeMismatchError, InvalidContainerTypeError
from .settings import Settings
from .types import TaskType


@dataclass
class TaskData:
    container: BlockContainer | ContractEvent | None
    handler: AsyncTaskiqDecoratedTask


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

        self.name = settings.APP_NAME

        network = settings.get_provider_context()
        # NOTE: This allows using connected ape methods e.g. `Contract`
        provider = network.__enter__()

        # Adjust defaults from connection
        if settings.NEW_BLOCK_TIMEOUT is None and (
            provider.network.name.endswith("-fork") or provider.network.name == LOCAL_NETWORK_NAME
        ):
            settings.NEW_BLOCK_TIMEOUT = int(timedelta(days=1).total_seconds())

        settings_str = "\n  ".join(f'{key}="{val}"' for key, val in settings.dict().items() if val)
        logger.info(f"Loading Silverback App with settings:\n  {settings_str}")

        self.broker = settings.get_broker()
        # NOTE: If no tasks registered yet, defaults to empty list instead of raising KeyError
        self.tasks: defaultdict[TaskType, list[TaskData]] = defaultdict(list)
        self.poll_settings: dict[str, dict] = {}

        atexit.register(network.__exit__, None, None, None)

        self.signer = settings.get_signer()
        self.new_block_timeout = settings.NEW_BLOCK_TIMEOUT
        self.start_block = settings.START_BLOCK

        self.network_choice = f"{provider.network.ecosystem.name}:{provider.network.name}"
        signer_str = f"\n  SIGNER={repr(self.signer)}"
        start_block_str = f"\n  START_BLOCK={self.start_block}" if self.start_block else ""
        new_block_timeout_str = (
            f"\n  NEW_BLOCK_TIMEOUT={self.new_block_timeout}" if self.new_block_timeout else ""
        )
        logger.info(
            f'Loaded Silverback App:\n  NETWORK="{self.network_choice}"'
            f"{signer_str}{start_block_str}{new_block_timeout_str}"
        )

        self.state = None  # Runner manages this

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
            (task_type is TaskType.NEW_BLOCKS and not isinstance(container, BlockContainer))
            or (task_type is TaskType.EVENT_LOG and not isinstance(container, ContractEvent))
            or (
                task_type
                not in (
                    TaskType.NEW_BLOCKS,
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
                if contract_address := getattr(container.contract, "address", None):
                    labels["contract_address"] = contract_address
                # NOTE: event signature is a string with spaces/commas, so encapsulate it in quotes
                labels["event_signature"] = f'"{container.abi.signature}"'

            broker_task = self.broker.register_task(
                handler,
                task_name=handler.__name__,
                **labels,
            )

            self.tasks[task_type].append(TaskData(container=container, handler=broker_task))
            return broker_task

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

            return self.broker_task_decorator(TaskType.NEW_BLOCKS, container=container)

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
