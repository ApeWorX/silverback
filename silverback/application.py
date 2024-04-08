import atexit
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable, Dict, Optional, Union

from ape.api.networks import LOCAL_NETWORK_NAME
from ape.contracts import ContractEvent, ContractInstance
from ape.logging import logger
from ape.managers.chain import BlockContainer
from ape.utils import ManagerAccessMixin
from taskiq import AsyncTaskiqDecoratedTask, TaskiqEvents

from .exceptions import InvalidContainerTypeError
from .settings import Settings
from .types import TaskType


@dataclass
class Task:
    container: Union[BlockContainer, ContractEvent, None]
    handler: AsyncTaskiqDecoratedTask


class TaskCollection(dict):
    def insert(self, task_type: TaskType, task: Task):
        if not isinstance(task_type, TaskType):
            raise ValueError("Unexpected key type")

        elif not isinstance(task, Task):
            raise ValueError("Unexpected value type")

        elif task_type is TaskType.NEW_BLOCKS and not isinstance(task.container, BlockContainer):
            raise ValueError("Mismatch between key and value types")

        elif task_type is TaskType.EVENT_LOG and not isinstance(task.container, ContractEvent):
            raise ValueError("Mismatch between key and value types")

        task_list = super().get(task_type) or []
        super().__setitem__(task_type, task_list + [task])


class SilverbackApp(ManagerAccessMixin):
    """
    The application singleton. Must be initialized prior to use.

    Usage example::

        from silverback import SilverbackApp

        app = SilverbackApp()

        ...  # Connection has been initialized, can call broker methods e.g. `app.on_(...)`
    """

    def __init__(self, settings: Optional[Settings] = None):
        """
        Create app

        Args:
            settings (Optional[~:class:`silverback.settings.Settings`]): Settings override.
                Defaults to environment settings.
        """
        if not settings:
            settings = Settings()

        self.network = settings.get_provider_context()
        # NOTE: This allows using connected ape methods e.g. `Contract`
        provider = self.network.__enter__()

        # Adjust defaults from connection
        if settings.NEW_BLOCK_TIMEOUT is None and (
            provider.network.name.endswith("-fork") or provider.network.name == LOCAL_NETWORK_NAME
        ):
            settings.NEW_BLOCK_TIMEOUT = int(timedelta(days=1).total_seconds())

        settings_str = "\n  ".join(f'{key}="{val}"' for key, val in settings.dict().items() if val)
        logger.info(f"Loading Silverback App with settings:\n  {settings_str}")

        self.broker = settings.get_broker()
        self.tasks = TaskCollection()
        self.poll_settings: Dict[str, Dict] = {}

        atexit.register(self.network.__exit__, None, None, None)

        self.signer = settings.get_signer()
        self.new_block_timeout = settings.NEW_BLOCK_TIMEOUT
        self.start_block = settings.START_BLOCK

        network_str = f'\n  NETWORK="{provider.network.ecosystem.name}:{provider.network.name}"'
        signer_str = f"\n  SIGNER={repr(self.signer)}"
        start_block_str = f"\n  START_BLOCK={self.start_block}" if self.start_block else ""
        new_block_timeout_str = (
            f"\n  NEW_BLOCK_TIMEOUT={self.new_block_timeout}" if self.new_block_timeout else ""
        )
        logger.info(
            f"Loaded Silverback App:{network_str}"
            f"{signer_str}{start_block_str}{new_block_timeout_str}"
        )

    def broker_task_decorator(
        self,
        task_type: TaskType,
        container: Union[BlockContainer, ContractEvent, None] = None,
    ):
        def add_taskiq_task(handler: Callable):
            # TODO: Support generic registration
            task = self.broker.register_task(
                handler,
                task_name=handler.__name__,
                task_type=str(task_type),
            )
            self.tasks.insert(task_type, Task(container=container, handler=task))
            return task

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
        container: Union[BlockContainer, ContractEvent],
        new_block_timeout: Optional[int] = None,
        start_block: Optional[int] = None,
    ):
        """
        Create task to handle events created by `container`.

        Args:
            container: (Union[BlockContainer, ContractEvent]): The event source to watch.
            new_block_timeout: (Optional[int]): Override for block timeout that is acceptable.
                Defaults to whatever the app's settings are for default polling timeout are.
            start_block (Optional[int]): block number to start processing events from.
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
