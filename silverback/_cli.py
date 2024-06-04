import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

import click
from ape.cli import (
    AccountAliasPromptChoice,
    ConnectedProviderCommand,
    ape_cli_context,
    network_option,
    verbosity_option,
)
from ape.exceptions import Abort
from fief_client.integrations.cli import FiefAuthNotAuthenticatedError
from taskiq import AsyncBroker
from taskiq.cli.worker.run import shutdown_broker
from taskiq.receiver import Receiver

from silverback._importer import import_from_string
from silverback.platform.client import DEFAULT_PROFILE, PlatformClient
from silverback.platform.types import ClusterConfiguration, ClusterTier
from silverback.runner import PollingRunner, WebsocketRunner


@click.group()
def cli():
    """Work with Silverback applications in local context (using Ape)."""


def _runner_callback(ctx, param, val):
    if not val:
        return None

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
    "runner_class",
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
def run(cli_ctx, account, runner_class, recorder, max_exceptions, path):
    if not runner_class:
        # NOTE: Automatically select runner class
        if cli_ctx.provider.ws_uri:
            runner_class = WebsocketRunner
        elif cli_ctx.provider.http_uri:
            runner_class = PollingRunner
        else:
            raise click.BadOptionUsage(
                option_name="network", message="Network choice cannot support running app"
            )

    app = import_from_string(path)
    runner = runner_class(app, recorder=recorder, max_exceptions=max_exceptions)
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


def platform_client(display_userinfo: bool = True):
    def get_client(ctx, param, value) -> PlatformClient:
        client = PlatformClient(profile_name=value)

        # NOTE: We need to be authenticated to display userinfo
        if not display_userinfo:
            return client

        try:
            userinfo = client.userinfo  # cache this
        except FiefAuthNotAuthenticatedError as e:
            raise click.UsageError("Not authenticated, please use `silverback login` first.") from e

        user_id = userinfo["sub"]
        username = userinfo["fields"].get("username")
        click.echo(
            f"{click.style('INFO', fg='blue')}: "
            f"Logged in as '{click.style(username if username else user_id, bold=True)}'"
        )
        return client

    return click.option(
        "-p",
        "--profile",
        "platform_client",
        default=DEFAULT_PROFILE,
        callback=get_client,
        help="Profile to use for Authentication and Platform API Host.",
    )


class PlatformCommands(click.Group):
    # NOTE: Override so we get the list ordered by definition order
    def list_commands(self, ctx: click.Context) -> list[str]:
        return list(self.commands)

    def command(self, *args, display_userinfo=True, **kwargs):
        profile_option = platform_client(display_userinfo=display_userinfo)
        outer = super().command

        def decorator(fn):
            return outer(*args, **kwargs)(profile_option(fn))

        return decorator


@cli.group(cls=PlatformCommands)
def cluster():
    """Connect to hosted application clusters"""


@cluster.command(display_userinfo=False)  # Otherwise would fail because not authorized
def login(platform_client: PlatformClient):
    """Login to hosted clusters"""
    platform_client.auth.authorize()
    userinfo = platform_client.userinfo  # cache this
    user_id = userinfo["sub"]
    username = userinfo["fields"]["username"]
    click.echo(
        f"{click.style('SUCCESS', fg='green')}: Logged in as "
        f"'{click.style(username, bold=True)}' (UUID: {user_id})"
    )


@cluster.command(name="list")
def list_clusters(platform_client: PlatformClient):
    """List available clusters"""
    if clusters := platform_client.clusters:
        for cluster_name, cluster_info in clusters.items():
            click.echo(f"{cluster_name}:")
            click.echo(f"  status: {cluster_info.status}")
            click.echo("  configuration:")
            click.echo(f"    cpu: {256 * 2 ** cluster_info.configuration.cpu / 1024} vCPU")
            memory_display = (
                f"{cluster_info.configuration.memory} GB"
                if cluster_info.configuration.memory > 0
                else "512 MiB"
            )
            click.echo(f"    memory: {memory_display}")
            click.echo(f"    networks: {cluster_info.configuration.networks}")
            click.echo(f"    bots: {cluster_info.configuration.bots}")
            click.echo(f"    triggers: {cluster_info.configuration.triggers}")

    else:
        click.secho("No clusters for this account", bold=True, fg="red")


@cluster.command(name="new")
@click.option(
    "-n",
    "--name",
    "cluster_name",
    default="",
    help="Name for new cluster (Defaults to random)",
)
@click.option(
    "-t",
    "--tier",
    default=ClusterTier.PERSONAL.name,
)
@click.option("-c", "--config", "config_updates", type=(str, str), multiple=True)
def new_cluster(
    platform_client: PlatformClient,
    cluster_name: str,
    tier: str,
    config_updates: list[tuple[str, str]],
):
    """Create a new cluster"""
    base_configuration = getattr(ClusterTier, tier.upper()).configuration()
    upgrades = ClusterConfiguration(
        **{k: int(v) if v.isnumeric() else v for k, v in config_updates}
    )
    cluster = platform_client.create_cluster(
        cluster_name=cluster_name,
        configuration=base_configuration | upgrades,
    )
    # TODO: Create a signature scheme for ClusterInfo
    #         (ClusterInfo configuration as plaintext, .id as nonce?)
    # TODO: Test payment w/ Signature validation of extra data
    click.echo(f"{click.style('SUCCESS', fg='green')}: Created '{cluster.name}'")


@cluster.command()
@click.option(
    "-c",
    "--cluster",
    "cluster_name",
    help="Name of cluster to connect with.",
    required=True,
)
def bots(platform_client: PlatformClient, cluster_name: str):
    """List all bots in a cluster"""
    if not (cluster := platform_client.clusters.get(cluster_name)):
        if clusters := "', '".join(platform_client.clusters):
            message = f"'{cluster_name}' is not a valid cluster, must be one of: '{clusters}'."

        else:
            suggestion = (
                "Check out https://silverback.apeworx.io "
                "for more information on how to get started"
            )
            message = "You have no valid clusters to chose from\n\n" + click.style(
                suggestion, bold=True
            )
        raise click.BadOptionUsage(
            option_name="cluster_name",
            message=message,
        )

    if bots := cluster.bots:
        click.echo("Available Bots:")
        for bot_name, bot_info in bots.items():
            click.echo(f"- {bot_name} (UUID: {bot_info.id})")

    else:
        click.secho("No bots in this cluster", bold=True, fg="red")
