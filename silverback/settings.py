from typing import Any

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


class Settings(BaseSettings, ManagerAccessMixin):
    """
    Settings for the Silverback app.

    Can override these settings from a default state, typically for advanced
    testing or deployment purposes. Defaults to a working in-memory broker.
    """

    # A unique identifier for this silverback instance
    APP_NAME: str = "bot"

    BROKER_CLASS: str = "taskiq:InMemoryBroker"
    BROKER_URI: str = ""  # To be deprecated in 0.6
    BROKER_KWARGS: dict[str, Any] = dict()

    ENABLE_METRICS: bool = False

    RESULT_BACKEND_CLASS: str = ""
    RESULT_BACKEND_URI: str = ""  # To be deprecated in 0.6
    RESULT_BACKEND_KWARGS: dict[str, Any] = dict()

    NETWORK_CHOICE: str = ""
    SIGNER_ALIAS: str = ""

    NEW_BLOCK_TIMEOUT: int | None = None

    # Used for recorder
    RECORDER_CLASS: str | None = None

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

        if self.RESULT_BACKEND_URI:
            # TODO: Maybe add a deprecation warning here for v0.6
            return result_backend_cls(self.RESULT_BACKEND_URI)

        backend_kwargs = self.RESULT_BACKEND_KWARGS
        return result_backend_cls(**backend_kwargs)

    def get_broker(self) -> AsyncBroker:
        broker_class = import_from_string(self.BROKER_CLASS)
        if broker_class == InMemoryBroker:
            broker = broker_class()

        else:
            broker_kwargs = self.BROKER_KWARGS

            if self.BROKER_URI:
                # TODO: Maybe add a deprecation warning here for v0.6
                broker_kwargs["url"] = self.BROKER_URI

            broker = broker_class(**broker_kwargs)

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

        recorder_class = import_from_string(recorder_cls_str)
        return recorder_class()

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

        # NOTE: Will only have a signer if assigned one here (or in app)
        signer = self.account_manager.load(alias)

        # NOTE: Set autosign if it's a keyfile account (for local testing)
        if isinstance(signer, KeyfileAccount):
            signer.set_autosign(True)

        return signer
