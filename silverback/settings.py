from typing import List, Optional

from ape.api import AccountAPI, ProviderContextManager
from ape.utils import ManagerAccessMixin
from pydantic import BaseSettings
from taskiq import AsyncBroker, InMemoryBroker, PrometheusMiddleware, TaskiqMiddleware

from ._importer import import_from_string
from .middlewares import SilverbackMiddleware


class Settings(BaseSettings, ManagerAccessMixin):
    """
    Settings for the Silverback app.

    Can override these settings from a default state, typically for advanced
    testing or deployment purposes. Defaults to a working in-memory broker.
    """

    BROKER_CLASS: str = "taskiq:InMemoryBroker"
    BROKER_URI: str = ""

    ENABLE_METRICS: bool = False

    RESULT_BACKEND_CLASS: str = ""
    RESULT_BACKEND_URI: str = ""

    NETWORK_CHOICE: str = ""
    SIGNER_ALIAS: str = ""

    NEW_BLOCK_TIMEOUT: Optional[int] = None
    START_BLOCK: Optional[int] = None

    class Config:
        env_prefix = "SILVERBACK_"
        case_sensitive = True

    def get_broker(self) -> AsyncBroker:
        broker_class = import_from_string(self.BROKER_CLASS)
        if broker_class == InMemoryBroker:
            broker = broker_class()

        else:
            broker = broker_class(self.BROKER_URI)

        middlewares: List[TaskiqMiddleware] = [SilverbackMiddleware()]

        if self.ENABLE_METRICS:
            middlewares.append(
                PrometheusMiddleware(server_addr="0.0.0.0", server_port=9000),
            )

        broker = broker.with_middlewares(*middlewares)

        if self.RESULT_BACKEND_CLASS:
            result_backend_class = import_from_string(self.RESULT_BACKEND_CLASS)
            result_backend = result_backend_class(self.RESULT_BACKEND_URI)
            broker = broker.with_result_backend(result_backend)

        return broker

    def get_provider_context(self) -> ProviderContextManager:
        return self.network_manager.parse_network_choice(
            self.NETWORK_CHOICE or self.network_manager.default_ecosystem.name
        )

    def get_signer(self) -> Optional[AccountAPI]:
        if self.SIGNER_ALIAS:
            if self.SIGNER_ALIAS.startswith("TEST::"):
                acct_idx = int(self.SIGNER_ALIAS.replace("TEST::", ""))
                return self.account_manager.test_accounts[acct_idx]

            # NOTE: Will only have a signer if assigned one here (or in app)
            return self.account_manager.load(self.SIGNER_ALIAS)

        # NOTE: Useful if user wants to add a "paper trading" mode
        return None
