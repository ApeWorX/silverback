from typing import TYPE_CHECKING, Any

from ape.api import AccountAPI, ProviderContextManager
from ape.utils import ManagerAccessMixin
from ape_accounts import KeyfileAccount
from pydantic_settings import BaseSettings, SettingsConfigDict
from taskiq import (
    AsyncBroker,
    AsyncResultBackend,
    InMemoryBroker,
    PrometheusMiddleware,
    TaskiqMiddleware,
)

from ._importer import import_from_string
from .middlewares import SilverbackMiddleware
from .recorder import BaseRecorder

if TYPE_CHECKING:
    from .cluster.client import ClusterClient


class Settings(BaseSettings, ManagerAccessMixin):
    """
    Settings for the Silverback bot.

    Can override these settings from a default state, typically for advanced
    testing or deployment purposes. Defaults to a working in-memory broker.
    """

    # A unique identifier for this silverback instance
    BOT_NAME: str = "bot"

    # Execute every handler using an independent fork context
    # NOTE: Requires fork-able provider installed and configured for network
    FORK_MODE: bool = False

    BROKER_CLASS: str = "taskiq:InMemoryBroker"
    BROKER_KWARGS: dict[str, Any] = dict()

    ENABLE_METRICS: bool = False

    RESULT_BACKEND_CLASS: str = "taskiq.brokers.inmemory_broker:InmemoryResultBackend"
    RESULT_BACKEND_KWARGS: dict[str, Any] = dict()

    NETWORK_CHOICE: str = ""
    SIGNER_ALIAS: str = ""

    # Used for recorder
    RECORDER_CLASS: str | None = None

    # Used for cluster access
    CLUSTER_URI: str | None = None
    CLUSTER_API_KEY: str | None = None

    model_config = SettingsConfigDict(env_prefix="SILVERBACK_", case_sensitive=True)

    def get_middlewares(self) -> list[TaskiqMiddleware]:
        middlewares: list[TaskiqMiddleware] = [
            # Built-in middlewares (required)
            SilverbackMiddleware(silverback_settings=self),
        ]

        if self.ENABLE_METRICS:
            middlewares.append(
                PrometheusMiddleware(server_addr="0.0.0.0", server_port=9000),
            )

        return middlewares

    def get_result_backend(self) -> AsyncResultBackend | None:
        if not self.RESULT_BACKEND_CLASS:
            return None

        result_backend_cls = import_from_string(self.RESULT_BACKEND_CLASS)
        return result_backend_cls(**self.RESULT_BACKEND_KWARGS)

    def get_broker(self) -> AsyncBroker:
        broker_class = import_from_string(self.BROKER_CLASS)
        if broker_class == InMemoryBroker:
            broker = broker_class()

        else:
            broker = broker_class(**self.BROKER_KWARGS)

        if middlewares := self.get_middlewares():
            broker = broker.with_middlewares(*middlewares)

        if result_backend := self.get_result_backend():
            broker = broker.with_result_backend(result_backend)

        return broker

    def get_network_choice(self) -> str:
        return self.NETWORK_CHOICE or self.network_manager.network.choice

    def get_recorder(self) -> BaseRecorder | None:
        if not (recorder_cls_str := self.RECORDER_CLASS):
            return None

        return import_from_string(recorder_cls_str)

    def get_provider_context(self) -> ProviderContextManager:
        # NOTE: Bit of a workaround for adhoc connections:
        #       https://github.com/ApeWorX/ape/issues/1762
        if "adhoc" in (network_choice := self.get_network_choice()):
            return ProviderContextManager(provider=self.provider)
        return self.network_manager.parse_network_choice(network_choice)

    def get_signer(self) -> AccountAPI | None:
        if not (alias := self.SIGNER_ALIAS):
            # NOTE: Useful if user wants to add a "paper trading" mode
            return None

        if alias.startswith("TEST::"):
            acct_idx = int(alias.replace("TEST::", ""))
            return self.account_manager.test_accounts[acct_idx]

        # NOTE: Will only have a signer if assigned one here (or in bot)
        signer = self.account_manager.load(alias)

        # NOTE: Set autosign if it's a keyfile account (for local testing)
        if isinstance(signer, KeyfileAccount):
            signer.set_autosign(True)

        return signer

    def get_cluster_client(self) -> "ClusterClient | None":
        from .cluster.client import ClusterClient

        if self.CLUSTER_URI and self.CLUSTER_API_KEY:
            return ClusterClient(
                base_url=self.CLUSTER_URI,
                headers={"X-API-Key": self.CLUSTER_API_KEY},
            )

        return None
