import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import click
from ape.cli import (
    AccountAliasPromptChoice,
    ConnectedProviderCommand,
    ape_cli_context,
    network_option,
    verbosity_option,
)
from ape.exceptions import Abort
from taskiq import AsyncBroker
from taskiq.brokers.inmemory_broker import InMemoryBroker
from taskiq.cli.worker.run import shutdown_broker
from taskiq.kicker import AsyncKicker
from taskiq.receiver import Receiver

from silverback._importer import import_from_string
from silverback.runner import PollingRunner
from silverback.settings import Settings
from silverback.types import ScalarType, TaskType, is_scalar_type


@click.group()
def cli():
    """Work with Silverback applications in local context (using Ape)."""


def _runner_callback(ctx, param, val):
    if not val:
        return PollingRunner

    elif runner := import_from_string(val):
        return runner

    raise ValueError(f"Failed to import runner '{val}'.")


def _recorder_callback(ctx, param, val):
    if not val:
        return None

    elif recorder := import_from_string(val):
        return recorder()

    raise ValueError(f"Failed to import recorder '{val}'.")


def _account_callback(ctx, param, val):
    if val:
        val = val.alias.replace("dev_", "TEST::")
        os.environ["SILVERBACK_SIGNER_ALIAS"] = val

    return val


def _network_callback(ctx, param, val):
    # NOTE: Make sure both of these have the same setting
    if env_network_choice := os.environ.get("SILVERBACK_NETWORK_CHOICE"):
        if val.network_choice != env_network_choice:
            raise Abort(
                f"Network choice '{val.network_choice}' does not "
                f"match environment variable '{env_network_choice}'."
            )

        # else it matches, no issue

    else:
        os.environ["SILVERBACK_NETWORK_CHOICE"] = val.network_choice

    return val


async def run_worker(broker: AsyncBroker, worker_count=2, shutdown_timeout=90):
    try:
        tasks = []
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            for _ in range(worker_count):
                receiver = Receiver(
                    broker=broker,
                    executor=pool,
                    validate_params=True,
                    max_async_tasks=1,
                    max_prefetch=0,
                )
                broker.is_worker_process = True
                tasks.append(receiver.listen())

            await asyncio.gather(*tasks)
    finally:
        await shutdown_broker(broker, shutdown_timeout)


@cli.command(cls=ConnectedProviderCommand, help="Run Silverback application client")
@ape_cli_context()
@verbosity_option()
@network_option(
    default=os.environ.get("SILVERBACK_NETWORK_CHOICE", "auto"),
    callback=_network_callback,
)
@click.option("--account", type=AccountAliasPromptChoice(), callback=_account_callback)
@click.option(
    "--runner",
    help="An import str in format '<module>:<CustomRunner>'",
    callback=_runner_callback,
)
@click.option(
    "--recorder",
    help="An import string in format '<module>:<CustomRecorder>'",
    callback=_recorder_callback,
)
@click.option("-x", "--max-exceptions", type=int, default=3)
@click.argument("path")
def run(cli_ctx, account, runner, recorder, max_exceptions, path):
    app = import_from_string(path)
    runner = runner(app, recorder=recorder, max_exceptions=max_exceptions)
    asyncio.run(runner.run())


@cli.command(cls=ConnectedProviderCommand, help="Run Silverback application task workers")
@ape_cli_context()
@verbosity_option()
@network_option(
    default=os.environ.get("SILVERBACK_NETWORK_CHOICE", "auto"),
    callback=_network_callback,
)
@click.option("--account", type=AccountAliasPromptChoice(), callback=_account_callback)
@click.option("-w", "--workers", type=int, default=2)
@click.option("-x", "--max-exceptions", type=int, default=3)
@click.option("-s", "--shutdown_timeout", type=int, default=90)
@click.argument("path")
def worker(cli_ctx, account, workers, max_exceptions, shutdown_timeout, path):
    app = import_from_string(path)
    asyncio.run(run_worker(app.broker, worker_count=workers, shutdown_timeout=shutdown_timeout))


class ScalarParam(click.ParamType):
    name = "scalar"

    def convert(self, val, param, ctx) -> ScalarType:
        if not isinstance(val, str) or is_scalar_type(val):
            return val

        elif val.lower() in ("f", "false"):
            return False

        elif val.lower() in ("t", "true"):
            return True

        try:
            return int(val)
        except Exception:
            pass

        try:
            return float(val)
        except Exception:
            pass

        # NOTE: Decimal allows the most values, so leave last
        return Decimal(val)


@cli.command(cls=ConnectedProviderCommand, help="Set parameters against a running silverback app")
@network_option(
    default=os.environ.get("SILVERBACK_NETWORK_CHOICE", "auto"),
    callback=_network_callback,
)
@click.option(
    "-p",
    "--param",
    "param_updates",
    type=(str, ScalarParam()),
    multiple=True,
)
def set_param(param_updates):

    if len(param_updates) > 1:
        task_name = str(TaskType._SET_PARAM_BATCH)
        arg = dict(param_updates)
    else:
        param_name, arg = param_updates[0]
        task_name = f"{TaskType._SET_PARAM}:{param_name}"

    async def set_parameters():
        broker = Settings().get_broker()
        if isinstance(broker, InMemoryBroker):
            raise RuntimeError("Cannot use with default in-memory broker")

        kicker = AsyncKicker(task_name, broker, labels={})
        task = await kicker.kiq(arg)
        result = await task.wait_result()

        if result.is_err:
            click.echo(result.error)
        else:
            click.echo(result.return_value)

    asyncio.run(set_parameters())
