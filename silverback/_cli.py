import asyncio
import os

import click
from ape.cli import NetworkBoundCommand, network_option

from silverback._importer import import_from_string
from silverback.runner import LiveRunner


@click.group()
def cli():
    """Work with SilverBack applications in local context (using Ape)."""


@cli.command(cls=NetworkBoundCommand)
@network_option()
@click.option("-x", "--max-exceptions", type=int, default=3)
@click.argument("path")
def run(network, max_exceptions, path):
    os.environ["SILVERBACK_NETWORK_CHOICE"] = network

    app = import_from_string(path)
    runner = LiveRunner(app, max_exceptions=max_exceptions)
    asyncio.run(runner.run())
