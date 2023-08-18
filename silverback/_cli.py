import asyncio
import os

import click
from ape.cli import AccountAliasPromptChoice, ape_cli_context, network_option, verbosity_option

from silverback._importer import import_from_string
from silverback.runner import LiveRunner


@click.group()
def cli():
    """Work with SilverBack applications in local context (using Ape)."""


@cli.command()
@ape_cli_context()
@verbosity_option()
@network_option(default=None)
@click.option("--account", type=AccountAliasPromptChoice(), default=None)
@click.option("-x", "--max-exceptions", type=int, default=3)
@click.argument("path")
def run(cli_ctx, network, account, max_exceptions, path):
    if network:
        os.environ["SILVERBACK_NETWORK_CHOICE"] = network
    else:
        network = os.environ.get("SILVERBACK_NETWORK_CHOICE", "")

    if account:
        os.environ["SILVERBACK_SIGNER_ALIAS"] = account.alias.replace("dev_", "TEST::")

    with cli_ctx.network_manager.parse_network_choice(network):
        app = import_from_string(path)
        runner = LiveRunner(app, max_exceptions=max_exceptions)
        asyncio.run(runner.run())
