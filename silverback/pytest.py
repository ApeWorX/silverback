import asyncio
import os
from pathlib import Path

import pytest
import yaml  # type: ignore[import]
from ape.utils import cached_property

from silverback._importer import import_from_string
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
        if not (network_triple := raw.get("network")):
            raise ValueError(f"{self.path} is missing key 'network'.")

        start_block = raw.get("start_block", 0)
        stop_block = raw.get("stop_block", -1)
        assertion_checks = raw.get("assertions", {})

        raw_bot_paths = raw.get("bots")
        if isinstance(raw_bot_paths, list):
            for bot_path in raw_bot_paths:
                if ":" in bot_path:
                    bot_path, bot_name = bot_path.split(":")
                    bot_path = Path(bot_path)
                else:
                    bot_path = Path(bot_path)
                    bot_name = "bot"

                yield BacktestItem.from_parent(
                    self,
                    name=f"{self.name}[{bot_name}]",
                    file_path=self.path,
                    bot_path=bot_path,
                    bot_name=bot_name,
                    network_triple=network_triple,
                    start_block=start_block,
                    stop_block=stop_block,
                    assertion_checks=assertion_checks,
                )

        else:
            if ":" in raw_bot_paths:
                bot_path, bot_name = raw_bot_paths.split(":")
                bot_path = Path(bot_path)
            else:
                bot_path = Path(raw_bot_paths)
                bot_name = "bot"

            yield BacktestItem.from_parent(
                self,
                name=self.name,
                file_path=self.path,
                bot_path=bot_path,
                bot_name=bot_name,
                network_triple=network_triple,
                start_block=start_block,
                stop_block=stop_block,
                assertion_checks=assertion_checks,
            )


class BacktestItem(pytest.Item):
    def __init__(
        self,
        *,
        file_path,
        bot_path,
        bot_name,
        network_triple,
        start_block,
        stop_block,
        assertion_checks,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.file_path = file_path
        self.bot_path = bot_path
        self.bot_name = bot_name
        self.network_triple = network_triple
        self.start_block = start_block
        self.stop_block = stop_block
        self.assertion_checks = assertion_checks

        self.assertion_failures = 0
        self.overruns = 0

    @cached_property
    def runner(self):
        os.environ["SILVERBACK_NETWORK_CHOICE"] = self.network_triple
        os.environ["PYTHONPATH"] = str(self.bot_path.parent)
        app = import_from_string(f"{self.bot_path.stem}:{self.bot_name}")
        return BacktestRunner(app, start_block=self.start_block, stop_block=self.stop_block)

    def check_assertions(self, result: dict):
        pass

    def runtest(self):
        asyncio.run(self.runner.run())
        self.raise_run_status()

    def raise_run_status(self):
        if self.overruns > 0 or self.assertion_failures > 0:
            raise AssertionViolation()
