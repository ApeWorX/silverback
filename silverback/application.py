import atexit
from datetime import timedelta
from typing import Callable, Dict, Optional, Union

from ape.api.networks import LOCAL_NETWORK_NAME
from ape.contracts import ContractEvent, ContractInstance
from ape.logging import logger
from ape.managers.chain import BlockContainer
from ape.types import AddressType
from ape.utils import ManagerAccessMixin
from taskiq import AsyncTaskiqDecoratedTask, TaskiqEvents

from .exceptions import DuplicateHandlerError, InvalidContainerTypeError
from .settings import Settings


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

        # Adjust defaults from connection
        if settings.NEW_BLOCK_TIMEOUT is None and (
            self.chain_manager.provider.network.name.endswith("-fork")
            or self.chain_manager.provider.network.name == LOCAL_NETWORK_NAME
        ):
            settings.NEW_BLOCK_TIMEOUT = int(timedelta(days=1).total_seconds())

        settings_str = "\n  ".join(f'{key}="{val}"' for key, val in settings.dict().items() if val)
        logger.info(f"Loading Silverback App with settings:\n  {settings_str}")

        self.broker = settings.get_broker()
        self.contract_events: Dict[AddressType, Dict[str, ContractEvent]] = {}
        self.poll_settings: Dict[str, Dict] = {}

        self.network = settings.get_provider_context()
        # NOTE: This allows using connected ape methods e.g. `Contract`
        provider = self.network.__enter__()

        atexit.register(self.network.__exit__)

        self.signer = settings.get_signer()
        self.new_block_timeout = settings.NEW_BLOCK_TIMEOUT
        self.start_block = settings.START_BLOCK

        network_str = f'\n  NETWORK="{provider.network.ecosystem.name}:{provider.network.name}"'
        signer_str = f"\n  SIGNER={repr(self.signer)}"
        start_block_str = f"\n  START_BLOCK={self.start_block}" if self.start_block else ""
        new_bock_timeout_str = (
            f"\n  NEW_BLOCK_TIMEOUT={self.new_block_timeout}" if self.new_block_timeout else ""
        )
        logger.info(
            f"Loaded Silverback App:{network_str}"
            f"{signer_str}{start_block_str}{new_bock_timeout_str}"
        )

    def on_startup(self) -> Callable:
        """
        Code to execute on worker startup / restart after an error.

        Usage example::

            @app.on_startup()
            def do_something_on_startup(state):
                ...  # Can provision resources, or add things to `state`.
        """
        return self.broker.on_event(TaskiqEvents.WORKER_STARTUP)

    def on_shutdown(self) -> Callable:
        """
        Code to execute on normal worker shutdown.

        Usage example::

            @app.on_shutdown()
            def do_something_on_shutdown(state):
                ...  # Update some external service, perhaps using information from `state`.
        """
        return self.broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)

    def get_block_handler(self) -> Optional[AsyncTaskiqDecoratedTask]:
        """
        Get access to the handler for `block` events.

        Returns:
            Optional[AsyncTaskiqDecoratedTask]: Returns decorated task, if one has been created.
        """
        return self.broker.available_tasks.get("block")

    def get_event_handler(
        self, event_target: AddressType, event_name: str
    ) -> Optional[AsyncTaskiqDecoratedTask]:
        """
        Get access to the handler for `<event_target>:<event_name>` events.

        Args:
            event_target (AddressType): The contract address of the target.
            event_name: (str): The name of the event emitted by ``event_target``.

        Returns:
            Optional[AsyncTaskiqDecoratedTask]: Returns decorated task, if one has been created.
        """
        return self.broker.available_tasks.get(f"{event_target}/event/{event_name}")

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
            new_block_timeout: (Optional[int]): Override for block timeoui that is acceptable.
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
            if self.get_event_handler(container.contract.address, container.abi.name):
                raise DuplicateHandlerError(
                    f"event {container.contract.address}:{container.abi.name}"
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

            return self.broker.task(
                task_name=f"{container.contract.address}/event/{container.abi.name}"
            )

        # TODO: Support account transaction polling
        # TODO: Support mempool polling
        raise InvalidContainerTypeError(container)
