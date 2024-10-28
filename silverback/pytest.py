import asyncio
import os
from functools import cached_property
from pathlib import Path

import pytest
import yaml  # type: ignore[import]
from ape import networks

from silverback._importer import import_from_string
from silverback.exceptions import SilverbackException
from silverback.recorder import TaskResult
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

        # NOTE: Use default assumption rest of CLI uses
        raw_bot_paths = raw.get("bots", "bot:bot")
        if raw_bot_paths == "*":
            raw_bot_paths = list(f.stem for f in (Path.cwd() / "bots").glob("*.py"))

        if isinstance(raw_bot_paths, list):
            for bot_path in raw_bot_paths:
                if ":" in bot_path:
                    bot_path, bot_name = bot_path.split(":")
                else:
                    bot_name = "bot"

                yield BacktestItem.from_parent(
                    self,
                    name=f"{self.path.stem}[{bot_path}]",
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
            else:
                bot_path = raw_bot_paths
                bot_name = "bot"

            yield BacktestItem.from_parent(
                self,
                name=self.path.stem,
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
        self.total_tasks = 0
        self.task_failures = 0

    def check_assertions(self, result: TaskResult):
        self.total_tasks += 1

        if result.execution_time > float(self.assertion_checks.get("execution_time", "inf")):
            self.overruns += 1

        if result.error:
            self.task_failures += 1

    @cached_property
    def runner(self) -> BacktestRunner:
        # NOTE: Set parameters for loading settings properly
        os.environ["SILVERBACK_BOT_NAME"] = self.bot_path
        with networks.parse_network_choice(self.network_triple):
            # NOTE: Loading bot requires a network connection
            bot = import_from_string(f"{self.bot_path}:{self.bot_name}")

        return BacktestRunner(
            bot,
            start_block=self.start_block,
            stop_block=self.stop_block,
            network_triple=self.network_triple,
            check_assertions=self.check_assertions,
        )

    def runtest(self):
        asyncio.run(self.runner.run())

        # Test is over, check test status (if it completed)
        self.raise_run_status()

    def raise_run_status(self):
        if self.overruns > 0 or self.assertion_failures > 0:
            raise AssertionViolation()

    def reportinfo(self):
        return self.path, None, self.name
