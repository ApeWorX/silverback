import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

import click
from ape.cli import AccountAliasPromptChoice, ape_cli_context, network_option, verbosity_option
from taskiq import AsyncBroker
from taskiq.cli.worker.run import shutdown_broker
from taskiq.receiver import Receiver

from silverback._importer import import_from_string
from silverback.runner import PollingRunner


@click.group()
def cli():
    """Work with Silverback applications in local context (using Ape)."""


def _runner_callback(ctx, param, val):
    if not val:
        return PollingRunner

    elif runner := import_from_string(val):
        return runner

    raise ValueError(f"Failed to import runner '{val}'.")


def _account_callback(ctx, param, val):
    if val:
        val = val.alias.replace("dev_", "TEST::")
        os.environ["SILVERBACK_SIGNER_ALIAS"] = val

    return val


def _network_callback(ctx, param, val):
    if val:
        os.environ["SILVERBACK_NETWORK_CHOICE"] = val
    else:
        val = os.environ.get("SILVERBACK_NETWORK_CHOICE", "")

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


@cli.command(help="Run Silverback application")
@ape_cli_context()
@verbosity_option()
@network_option(default=None, callback=_network_callback)
@click.option("--account", type=AccountAliasPromptChoice(), callback=_account_callback)
@click.option(
    "--runner",
    help="An import str in format '<module>:<CustomRunner>'",
    callback=_runner_callback,
)
@click.option("-x", "--max-exceptions", type=int, default=3)
@click.argument("path")
def run(cli_ctx, network, account, runner, max_exceptions, path):
    with cli_ctx.network_manager.parse_network_choice(network):
        app = import_from_string(path)
        runner = runner(app, max_exceptions=max_exceptions)
        asyncio.run(runner.run())


@cli.command(help="Run Silverback application task workers")
@ape_cli_context()
@verbosity_option()
@network_option(default=None, callback=_network_callback)
@click.option("--account", type=AccountAliasPromptChoice(), callback=_account_callback)
@click.option("-w", "--workers", type=int, default=2)
@click.option("-x", "--max-exceptions", type=int, default=3)
@click.option("-s", "--shutdown_timeout", type=int, default=90)
@click.argument("path")
def worker(cli_ctx, network, account, workers, max_exceptions, shutdown_timeout, path):
    app = import_from_string(path)
    asyncio.run(run_worker(app.broker, worker_count=workers, shutdown_timeout=shutdown_timeout))
