import contextlib
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from silverback._cli import cli as root_cli


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def cli():
    return root_cli


class Settings:
    def __init__(self, signer=None):
        self._signer = signer
        self.BOT_NAME = "bot"
        self.NEW_BLOCK_TIMEOUT = None
        self.FORK_MODE = False

    def get_provider_context(self):
        return contextlib.nullcontext(
            SimpleNamespace(
                network=SimpleNamespace(
                    name="mainnet",
                    ecosystem=SimpleNamespace(name="ethereum"),
                ),
                network_manager=SimpleNamespace(fork=contextlib.nullcontext()),
            )
        )

    def get_broker(self):
        def register_task(*_a, **_k):
            return object()

        def on_event(*_a, **_k):
            def deco(fn):
                return fn

            return deco

        return SimpleNamespace(register_task=register_task, on_event=on_event)

    def get_signer(self):
        return self._signer

    def model_dump(self):
        return {}


@pytest.fixture
def signer():
    return SimpleNamespace(nonce=0, call=lambda txn, *a, **k: {"ok": True})


@pytest.fixture
def settings():
    def _make(signer=None):
        return Settings(signer=signer)

    return _make
