import asyncio
import os
from pathlib import Path
from time import time

import pytest
import yaml  # type: ignore[import]
from ape import chain, networks
from ape.utils import cached_property
from uvicorn.importer import import_from_string

from silverback.exceptions import SilverbackException
from silverback.runner import BacktestRunner


class AssertionViolation(SilverbackException):
    pass


def pytest_collect_file(parent, file_path):
    if file_path.suffix == ".yaml" and file_path.name.startswith("backtest"):
        return BacktestFile.from_parent(parent, path=file_path)


class BacktestFile(pytest.File):
    def collect(self):
        raw = yaml.safe_load(self.path.open())
        yield BacktestItem.from_parent(
            self,
            name=self.name,
            file_path=self.path,
            app_path=raw.get("app", os.environ.get("SILVERBACK_APP")),
            network_triple=raw.get("network", ""),
            start_block=raw.get("start_block", 0),
            stop_block=raw.get("stop_block", -1),
            assertion_checks=raw.get("assertions", {}),
        )


class BacktestItem(pytest.Item):
    def __init__(
        self,
        *,
        file_path,
        app_path,
        network_triple,
        start_block,
        stop_block,
        assertion_checks,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.file_path = file_path
        self.app_path = app_path or "app.py:app"
        self.network_triple = network_triple
        self.start_block = start_block
        self.stop_block = stop_block
        self.assertion_checks = assertion_checks

        self.assertion_failures = 0
        self.overruns = 0

    @cached_property
    def runner(self):
        app_path, app_name = self.app_path.split(":")
        app_path = Path(app_path)
        os.environ["SILVERBACK_NETWORK_CHOICE"] = self.network_triple
        os.environ["PYTHONPATH"] = str(app_path.parent)
        app = import_from_string(f"{app_path.stem}:{app_name}")
        # Load backtest runner w/ app at start block
        return BacktestRunner(app, block_number=self.start_block)

    def check_assertions(self, result: dict):
        pass

    def runtest(self):
        for block_number in range(self.start_block, self.stop_block + 1):
            with networks.parse_network_choice(
                self.network_triple,
                provider_settings={"fork_block_number": block_number},
            ):
                start_time = time()
                asyncio.run(self.runner.run())
                # NOTE: BacktestRunner.run increments every time it's run
                if time() - start_time > chain.provider.network.block_time:
                    self.overruns += 1

        self.raise_run_status()

    def raise_run_status(self):
        if self.overruns > 0 or self.assertion_failures > 0:
            raise AssertionViolation()
