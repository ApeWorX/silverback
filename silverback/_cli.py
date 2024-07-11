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
from fief_client import Fief
from fief_client.integrations.cli import FiefAuth, FiefAuthNotAuthenticatedError
from taskiq import AsyncBroker
from taskiq.cli.worker.run import shutdown_broker
from taskiq.receiver import Receiver

from silverback._importer import import_from_string
from silverback.cluster.client import Client, ClusterClient, PlatformClient
from silverback.cluster.settings import (
    DEFAULT_PROFILE,
    PROFILE_PATH,
    ClusterProfile,
    PlatformProfile,
    ProfileSettings,
)
from silverback.cluster.types import ClusterConfiguration, ClusterTier
from silverback.runner import PollingRunner, WebsocketRunner


class OrderedCommands(click.Group):
    # NOTE: Override so we get the list ordered by definition order
    def list_commands(self, ctx: click.Context) -> list[str]:
        return list(self.commands)


@click.group(cls=OrderedCommands)
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


def get_auth(profile_name: str = DEFAULT_PROFILE) -> FiefAuth:
    settings = ProfileSettings.from_config_file()
    auth_info = settings.auth[profile_name]
    fief = Fief(auth_info.host, auth_info.client_id)
    return FiefAuth(fief, str(PROFILE_PATH.parent / f"{profile_name}.json"))


def display_login_message(auth: FiefAuth, host: str):
    userinfo = auth.current_user()

    user_id = userinfo["sub"]
    username = userinfo["fields"].get("username")
    click.echo(
        f"{click.style('INFO', fg='blue')}: "
        f"Logged in to '{click.style(host, bold=True)}' as "
        f"'{click.style(username if username else user_id, bold=True)}'"
    )


def client_option():
    settings = ProfileSettings.from_config_file()

    def get_client_from_profile(ctx, param, value) -> Client:
        if not (profile := settings.profile.get(value)):
            raise click.BadOptionUsage(option_name=param, message=f"Unknown profile '{value}'.")

        if isinstance(profile, PlatformProfile):
            auth = get_auth(profile.auth)

            try:
                display_login_message(auth, profile.host)
            except FiefAuthNotAuthenticatedError as e:
                raise click.UsageError(
                    "Not authenticated, please use `silverback login` first."
                ) from e

            return PlatformClient(
                base_url=profile.host,
                cookies=dict(session=auth.access_token_info()["access_token"]),
            )

        elif isinstance(profile, ClusterProfile):
            click.echo(
                f"{click.style('INFO', fg='blue')}: Logged in to "
                f"'{click.style(profile.host, bold=True)}' using API Key"
            )
            return ClusterClient(
                base_url=profile.host,
                headers={"X-API-Key": profile.api_key},
            )

        raise NotImplementedError  # Should not be possible, but mypy barks

    return click.option(
        "-p",
        "--profile",
        "client",
        default=DEFAULT_PROFILE,
        callback=get_client_from_profile,
        help="Profile to use for connecting to Cluster Host.",
    )


@cli.group(cls=OrderedCommands)
def cluster():
    """Connect to hosted application clusters"""


@cluster.command()
@click.option(
    "-a",
    "--auth-profile",
    "auth",
    default=DEFAULT_PROFILE,
    callback=lambda ctx, param, value: get_auth(value),
    help="Authentication profile to use for Platform login.",
)
def login(auth: FiefAuth):
    """Login to hosted clusters"""

    auth.authorize()
    display_login_message(auth, auth.client.base_url)


@cluster.command()
@client_option()
def workspaces(client: Client):
    """List available workspaces"""

    if not isinstance(client, PlatformClient):
        raise click.UsageError("This feature is not available when directly connected to a cluster")

    if workspaces := client.workspaces:
        for workspace_slug, workspace_info in workspaces.items():
            click.echo(f"{workspace_slug}:")
            click.echo(f"  id: {workspace_info.id}")
            click.echo(f"  name: {workspace_info.name}")
            click.echo(f"  owner: {workspace_info.owner_id}")

    else:
        click.secho(
            "No workspaces available for this account. "
            "Go to https://silverback.apeworx.io to sign up and create a new workspace",
            bold=True,
            fg="red",
        )


@cluster.command(name="list")
@client_option()
@click.option("-w", "--workspace", "workspace_name")
def list_clusters(client: Client, workspace_name: str):
    """List available clusters"""

    if not isinstance(client, PlatformClient):
        raise click.UsageError("This feature is not available when directly connected to a cluster")

    if not (workspace := client.workspaces.get(workspace_name)):
        raise click.BadOptionUsage("workspace_name", f"Unknown workspace '{workspace_name}'")

    if clusters := workspace.clusters:
        for cluster_slug, cluster_info in clusters.items():
            click.echo(f"{cluster_slug}:")
            click.echo(f"  name: {cluster_info.name}")
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
@client_option()
@click.option("-w", "--workspace", "workspace_name")
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
    client: Client,
    workspace_name: str,
    cluster_name: str,
    tier: str,
    config_updates: list[tuple[str, str]],
):
    """Create a new cluster"""

    if not isinstance(client, PlatformClient):
        raise click.UsageError("This feature is not available when directly connected to a cluster")

    if not (workspace := client.workspaces.get(workspace_name)):
        raise click.BadOptionUsage("workspace_name", f"Unknown workspace '{workspace_name}'")

    base_configuration = getattr(ClusterTier, tier.upper()).configuration()
    upgrades = ClusterConfiguration(
        **{k: int(v) if v.isnumeric() else v for k, v in config_updates}
    )
    cluster = workspace.create_cluster(
        cluster_name=cluster_name,
        configuration=base_configuration | upgrades,
    )
    # TODO: Create a signature scheme for ClusterInfo
    #         (ClusterInfo configuration as plaintext, .id as nonce?)
    # TODO: Test payment w/ Signature validation of extra data
    click.echo(f"{click.style('SUCCESS', fg='green')}: Created '{cluster.name}'")


@cluster.command()
@client_option()
@click.option("-w", "--workspace", "workspace_name")
@click.option(
    "-c",
    "--cluster",
    "cluster_name",
    help="Name of cluster to connect with.",
)
def bots(client: Client, workspace_name: str, cluster_name: str):
    """List all bots in a cluster"""
    if not isinstance(client, ClusterClient):
        client = client.get_cluster_client(workspace_name, cluster_name)

    if bots := client.bots:
        click.echo("Available Bots:")
        for bot_name, bot_info in bots.items():
            click.echo(f"- {bot_name} (UUID: {bot_info.id})")

    else:
        click.secho("No bots in this cluster", bold=True, fg="red")
