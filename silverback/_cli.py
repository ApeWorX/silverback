import asyncio
import os

import click
from ape.cli import AccountAliasPromptChoice, ape_cli_context, network_option, verbosity_option
from ape.logging import logger

from silverback._importer import import_from_string
from silverback.runner import LiveRunner


@click.group()
def cli():
    """Work with SilverBack applications in local context (using Ape)."""


def _load_runner(ctx, param, val):
    if not val:
        return LiveRunner

    elif runner := import_from_string(val):
        logger.info(f"Using custom runner '{runner.__name__}'.")
        return runner

    raise ValueError(f"Failed to import runner '{val}'.")


@cli.command()
@ape_cli_context()
@verbosity_option()
@network_option(default=None)
@click.option("--account", type=AccountAliasPromptChoice())
@click.option(
    "--runner",
    help="An import str in format '<module>:<CustomRunner>'",
    callback=_load_runner,
)
@click.option("-x", "--max-exceptions", type=int, default=3)
@click.argument("path")
def run(cli_ctx, network, account, runner, max_exceptions, path):
    if network:
        os.environ["SILVERBACK_NETWORK_CHOICE"] = network
    else:
        network = os.environ.get("SILVERBACK_NETWORK_CHOICE", "")

    if account:
        os.environ["SILVERBACK_SIGNER_ALIAS"] = account.alias.replace("dev_", "TEST::")

    with cli_ctx.network_manager.parse_network_choice(network):
        app = import_from_string(path)
        runner = runner(app, max_exceptions=max_exceptions)
        asyncio.run(runner.run())
