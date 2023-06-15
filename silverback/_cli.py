import asyncio
import os

import click
from ape.cli import AccountAliasPromptChoice, NetworkBoundCommand, network_option, verbosity_option

from silverback._importer import import_from_string
from silverback.runner import LiveRunner


@click.group()
def cli():
    """Work with SilverBack applications in local context (using Ape)."""


@cli.command(cls=NetworkBoundCommand)
@verbosity_option()
@network_option()
@click.option("--account", type=AccountAliasPromptChoice(), default=None)
@click.option("-x", "--max-exceptions", type=int, default=3)
@click.argument("path")
def run(network, account, max_exceptions, path):
    os.environ["SILVERBACK_NETWORK_CHOICE"] = network

    if account:
        os.environ["SILVERBACK_SIGNER_ALIAS"] = account.alias.replace("dev_", "TEST::")

    app = import_from_string(path)
    runner = LiveRunner(app, max_exceptions=max_exceptions)
    asyncio.run(runner.run())
