import asyncio
import os

import click
from ape.cli import AccountAliasPromptChoice, ape_cli_context, network_option, verbosity_option

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


@cli.command()
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
