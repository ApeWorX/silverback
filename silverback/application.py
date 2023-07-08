import atexit
from typing import Callable, Dict, Optional, Union

from ape.contracts import ContractEvent, ContractInstance
from ape.logging import logger
from ape.managers.chain import BlockContainer
from ape.types import AddressType
from ape.utils import ManagerAccessMixin
from taskiq import AsyncTaskiqDecoratedTask, TaskiqEvents

from .exceptions import DuplicateHandler, InvalidContainerType
from .settings import Settings


class SilverBackApp(ManagerAccessMixin):
    def __init__(self, settings: Optional[Settings] = None):
        """
        Create app
        """
        if not settings:
            settings = Settings()

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

        network_str = f'\n  NETWORK="{provider.network.ecosystem.name}:{provider.network.name}"'
        signer_str = f"\n  SIGNER={repr(self.signer)}"
        logger.info(f"Loaded Silverback App:{network_str}{signer_str}")

    def on_startup(self) -> Callable:
        """
        Code to execute on startup / restart after an error.
        """
        return self.broker.on_event(TaskiqEvents.WORKER_STARTUP)

    def on_shutdown(self) -> Callable:
        """
        Code to execute on normal shutdown.
        """
        return self.broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)

    def get_block_handler(self) -> Optional[AsyncTaskiqDecoratedTask]:
        return self.broker.available_tasks.get("block")

    def get_event_handler(
        self, event_target: AddressType, event_name: str
    ) -> Optional[AsyncTaskiqDecoratedTask]:
        return self.broker.available_tasks.get(f"{event_target}/event/{event_name}")

    def on_(
        self,
        container: Union[BlockContainer, ContractEvent],
        new_block_timeout: Optional[int] = None,
    ):
        if isinstance(container, BlockContainer):
            if self.get_block_handler():
                raise DuplicateHandler("block")

            if new_block_timeout is not None:
                if "_blocks_" in self.poll_settings:
                    self.poll_settings["_blocks_"]["new_block_timeout"] = new_block_timeout
                else:
                    self.poll_settings["_blocks_"] = {"new_block_timeout": new_block_timeout}

            return self.broker.task(task_name="block")

        elif isinstance(container, ContractEvent) and isinstance(
            container.contract, ContractInstance
        ):
            if self.get_event_handler(container.contract.address, container.abi.name):
                raise DuplicateHandler(f"event {container.contract.address}:{container.abi.name}")

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

            return self.broker.task(
                task_name=f"{container.contract.address}/event/{container.abi.name}"
            )

        # TODO: Support account transaction polling
        # TODO: Support mempool polling
        raise InvalidContainerType(container)
