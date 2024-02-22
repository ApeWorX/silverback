import atexit
from datetime import timedelta
from typing import Callable, Dict, List, Optional, Union

from ape.api.networks import LOCAL_NETWORK_NAME
from ape.contracts import ContractEvent, ContractInstance
from ape.logging import logger
from ape.managers.chain import BlockContainer
from ape.types import AddressType
from ape.utils import ManagerAccessMixin
from taskiq import AsyncTaskiqDecoratedTask, TaskiqEvents

from silverback.exceptions import DuplicateHandlerError, InvalidContainerTypeError
from silverback.settings import Settings
from silverback.types import EMPTY_HASH, EventInputFilter


def event_handler_path_base(
    contract_address: AddressType, event_name: str, input_filter: Optional[EventInputFilter] = None
) -> str:
    """Return a handler ID string for an event"""
    return f"{contract_address}/event/{event_name}"


def event_handler_path(
    contract_address: AddressType, event_name: str, input_filter: Optional[EventInputFilter] = None
) -> str:
    """Return a unique handler ID string for an event with specific inputs to match"""
    filter_suffix = f"{input_filter.filter_id}" if input_filter else EMPTY_HASH
    return f"{event_handler_path_base(contract_address, event_name)}/{filter_suffix}"


class SilverbackApp(ManagerAccessMixin):
    """
    The application singleton. Must be initialized prior to use.

    Usage example::

        from silverback import SilverbackApp

        app = SilverbackApp()

        ...  # Connection has been initialized, can call broker methods e.g. `app.on_(...)`
    """

    _inputer_filters: Dict[str, EventInputFilter] = {}

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
        self.contract_events: Dict[AddressType, Dict[str, ContractEvent]] = {}
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

    def on_startup(self) -> Callable:
        """
        Code to execute on one worker upon startup / restart after an error.

        Usage example::

            @app.on_startup()
            def do_something_on_startup(startup_state):
                ...  # Reprocess missed events or blocks
        """
        return self.broker.task(task_name="silverback_startup")

    def on_shutdown(self) -> Callable:
        """
        Code to execute on one worker at shutdown.

        Usage example::

            @app.on_shutdown()
            def do_something_on_shutdown():
                ...  # Record final state of app
        """
        return self.broker.task(task_name="silverback_shutdown")

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

    def get_startup_handler(self) -> Optional[AsyncTaskiqDecoratedTask]:
        """
        Get access to the handler for `silverback_startup` events.

        Returns:
            Optional[AsyncTaskiqDecoratedTask]: Returns decorated task, if one has been created.
        """
        return self.broker.find_task("silverback_startup")

    def get_shutdown_handler(self) -> Optional[AsyncTaskiqDecoratedTask]:
        """
        Get access to the handler for `silverback_shutdown` events.

        Returns:
            Optional[AsyncTaskiqDecoratedTask]: Returns decorated task, if one has been created.
        """
        return self.broker.find_task("silverback_shutdown")

    def get_block_handler(self) -> Optional[AsyncTaskiqDecoratedTask]:
        """
        Get access to the handler for `block` events.

        Returns:
            Optional[AsyncTaskiqDecoratedTask]: Returns decorated task, if one has been created.
        """
        return self.broker.find_task("block")

    def get_event_handler(
        self,
        event_target: AddressType,
        event_name: str,
        input_filter: Optional[EventInputFilter] = None,
    ) -> Optional[AsyncTaskiqDecoratedTask]:
        """
        Get access to the handler for `<event_target>:<event_name>:<input_params>` events.

        Args:
            event_target (AddressType): The contract address of the target.
            event_name (str): The name of the event emitted by ``event_target``.
            input_filter (Optional[EventInputFilter]): An input filter to match against

        Returns:
            Optional[AsyncTaskiqDecoratedTask]: Returns decorated task, if one has been created.
        """
        return self.broker.find_task(event_handler_path(event_target, event_name, input_filter))

    def get_event_handlers(
        self, event_target: AddressType, event_name: str
    ) -> List[AsyncTaskiqDecoratedTask]:
        """
        Get access to all handlers for `<event_target>:<event_name>` events.

        Args:
            event_target (AddressType): The contract address of the target.
            event_name: (str): The name of the event emitted by ``event_target``.

        Returns:
            Optional[AsyncTaskiqDecoratedTask]: Returns decorated task, if one has been created.
        """
        handler_path = event_handler_path_base(event_target, event_name)
        all_tasks = self.broker.get_all_tasks()
        return [
            all_tasks[k] for k in filter(lambda x: x.startswith(handler_path), all_tasks.keys())
        ]

    def get_input_filter(self, event_handler_path: str) -> Optional[EventInputFilter]:
        """
        Get the input filter for an event handler if it exists.

        Args:
            event_handler_path (str): The path-like task name for the handler

        Returns:
            Optional[EventInputFilter]: Returns event filter if it exists
        """
        return self._inputer_filters.get(event_handler_path)

    def on_(
        self,
        container: Union[BlockContainer, ContractEvent],
        new_block_timeout: Optional[int] = None,
        start_block: Optional[int] = None,
        **kwargs,
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
            if self.get_block_handler():
                raise DuplicateHandlerError("block")

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

            return self.broker.task(task_name="block")

        elif isinstance(container, ContractEvent) and isinstance(
            container.contract, ContractInstance
        ):
            input_filter = EventInputFilter.from_on_args(container, kwargs)

            if self.get_event_handler(container.contract.address, container.abi.name, input_filter):
                raise DuplicateHandlerError(
                    f"event {container.contract.address}:{container.abi.name}:{input_filter}"
                )

            key = container.contract.address
            if container.contract.address in self.contract_events:
                self.contract_events[key][container.abi.name] = container
            else:
                self.contract_events[key] = {container.abi.name: container}

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

            handler_name = event_handler_path(
                container.contract.address, container.abi.name, input_filter
            )

            if input_filter:
                self._inputer_filters[handler_name] = input_filter

            logger.debug(f"Creating event handler {handler_name}")
            return self.broker.task(task_name=handler_name)

        # TODO: Support account transaction polling
        # TODO: Support mempool polling
        raise InvalidContainerTypeError(container)
