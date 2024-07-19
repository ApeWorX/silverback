import asyncio
import os

import click
from ape.cli import (
    AccountAliasPromptChoice,
    ConnectedProviderCommand,
    ape_cli_context,
    network_option,
    verbosity_option,
)
from ape.exceptions import Abort
from fief_client.integrations.cli import FiefAuth

from silverback._click_ext import (
    AuthCommand,
    OrderedCommands,
    PlatformGroup,
    cls_import_callback,
    display_login_message,
)
from silverback._importer import import_from_string
from silverback.cluster.client import ClusterClient, PlatformClient
from silverback.cluster.types import ClusterTier, render_dict_as_yaml
from silverback.runner import PollingRunner, WebsocketRunner
from silverback.worker import run_worker


@click.group(cls=OrderedCommands)
def cli():
    """Work with Silverback applications in local context (using Ape)."""


# TODO: Make `silverback.settings.Settings` (to remove having to set envvars)
# TODO: Use `envvar=...` to be able to set the value of options from correct envvar
def _account_callback(ctx, param, val):
    if val:
        val = val.alias.replace("dev_", "TEST::")
        os.environ["SILVERBACK_SIGNER_ALIAS"] = val

    return val


# TODO: Make `silverback.settings.Settings` (to remove having to set envvars)
# TODO: Use `envvar=...` to be able to set the value of options from correct envvar
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
    callback=cls_import_callback,
)
@click.option(
    "--recorder",
    "recorder_class",
    help="An import string in format '<module>:<CustomRecorder>'",
    callback=cls_import_callback,
)
@click.option("-x", "--max-exceptions", type=int, default=3)
@click.argument("path")
def run(cli_ctx, account, runner_class, recorder_class, max_exceptions, path):
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
    runner = runner_class(
        app,
        recorder=recorder_class() if recorder_class else None,
        max_exceptions=max_exceptions,
    )
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


@cli.command(cls=AuthCommand)
def login(auth: FiefAuth):
    """
    CLI Login to Managed Authorization Service

    Initiate a login in to the configured service using the given auth PROFILE.
    Defaults to https://account.apeworx.io if PROFILE not provided.

    NOTE: You likely do not need to use an auth PROFILE here.
    """

    auth.authorize()
    display_login_message(auth, auth.client.base_url)


@cli.group(cls=PlatformGroup)
def cluster():
    """Connect to hosted application clusters"""


@cluster.command()
def workspaces(client: PlatformClient):
    """[Platform Only] List available workspaces"""

    if workspace_display := render_dict_as_yaml(client.workspaces):
        click.echo(workspace_display)

    else:
        click.secho(
            "No workspaces available for this account. "
            "Go to https://silverback.apeworx.io to sign up and create a new workspace",
            bold=True,
            fg="red",
        )


@cluster.command(name="list")
@click.argument("workspace")
def list_clusters(client: PlatformClient, workspace: str):
    """[Platform Only] List available clusters in WORKSPACE"""

    if not (workspace_client := client.workspaces.get(workspace)):
        raise click.BadOptionUsage("workspace", f"Unknown workspace '{workspace}'")

    if cluster_display := render_dict_as_yaml(workspace_client.clusters):
        click.echo(cluster_display)

    else:
        click.secho("No clusters for this account", bold=True, fg="red")


@cluster.command(name="new")
@click.option(
    "-n",
    "--name",
    "cluster_name",
    help="Name for new cluster (Defaults to random)",
)
@click.option(
    "-s",
    "--slug",
    "cluster_slug",
    help="Slug for new cluster (Defaults to name.lower())",
)
@click.option(
    "-t",
    "--tier",
    default=ClusterTier.PERSONAL.name,
    help="Named set of options to use for cluster (Defaults to PERSONAL)",
)
@click.option(
    "-c",
    "--config",
    "config_updates",
    type=(str, str),
    multiple=True,
    help="Config options to set for cluster (overrides value of -t/--tier)",
)
@click.argument("workspace")
def new_cluster(
    client: PlatformClient,
    workspace: str,
    cluster_name: str | None,
    cluster_slug: str | None,
    tier: str,
    config_updates: list[tuple[str, str]],
):
    """[Platform Only] Create a new cluster in WORKSPACE"""

    if not (workspace_client := client.workspaces.get(workspace)):
        raise click.BadOptionUsage("workspace", f"Unknown workspace '{workspace}'")

    configuration = getattr(ClusterTier, tier.upper()).configuration()

    for k, v in config_updates:
        setattr(configuration, k, int(v) if v.isnumeric() else v)

    try:
        cluster = workspace_client.create_cluster(
            cluster_name=cluster_name,
            cluster_slug=cluster_slug,
            configuration=configuration,
        )
        click.echo(f"{click.style('SUCCESS', fg='green')}: Created '{cluster.name}'")
    except RuntimeError as e:
        raise click.UsageError(str(e))

    # TODO: Pay for cluster via new stream


# `silverback cluster pay WORKSPACE/NAME --account ALIAS --time "10 days"`
# TODO: Create a signature scheme for ClusterInfo
#         (ClusterInfo configuration as plaintext, .id as nonce?)
# TODO: Test payment w/ Signature validation of extra data


@cluster.command(name="status")
def cluster_status(client: ClusterClient):
    """
    Get Status information about a CLUSTER

    For clusters on the Silverback Platform, please provide a name for the cluster to access using
    your platform authentication obtained via `silverback login` in `workspace/cluster-name` format

    NOTE: Connecting directly to clusters is supported, but is an advanced use case.
    """
    click.echo(render_dict_as_yaml(client.build_display_fields()))


@cluster.group(cls=OrderedCommands)
def env():
    """Commands for managing environment variables in CLUSTER"""


def parse_envvars(ctx, name, value: list[str]) -> dict[str, str]:
    def parse_envar(item: str):
        if not ("=" in item and len(item.split("=")) == 2):
            raise click.UsageError("Value '{item}' must be in form `NAME=VAL`")

        return item.split("=")

    return dict(parse_envar(item) for item in value)


@env.command(name="new")
@click.option(
    "-e",
    "--env",
    "variables",
    multiple=True,
    type=str,
    metavar="NAME=VAL",
    callback=parse_envvars,
    help="Environment variable key and value to add (Multiple allowed)",
)
@click.argument("name")
def new_env(client: ClusterClient, variables: dict, name: str):
    """Create a new GROUP of environment variables in CLUSTER"""
    if len(variables) == 0:
        raise click.UsageError("Must supply at least one var via `-e`")

    try:
        click.echo(render_dict_as_yaml(client.new_env(name=name, variables=variables)))

    except RuntimeError as e:
        raise click.UsageError(str(e))


@env.command(name="list")
def list_envs(client: ClusterClient):
    """List latest revisions of all variable groups in CLUSTER"""
    if all_envs := render_dict_as_yaml(client.envs):
        click.echo(all_envs)

    else:
        click.secho("No envs in this cluster", bold=True, fg="red")


@env.command()
@click.argument("name")
@click.argument("new_name")
def change_name(client: ClusterClient, name: str, new_name: str):
    """Change the display name of a variable GROUP in CLUSTER"""
    if not (env := client.envs.get(name)):
        raise click.UsageError(f"Unknown Variable Group '{name}'")

    click.echo(render_dict_as_yaml(env.update(name=new_name)))


@env.command(name="set")
@click.option(
    "-e",
    "--env",
    "updated_vars",
    multiple=True,
    type=str,
    metavar="NAME=VAL",
    callback=parse_envvars,
    help="Environment variable key and value to add/update (Multiple allowed)",
)
@click.option(
    "-d",
    "--del",
    "deleted_vars",
    multiple=True,
    type=str,
    metavar="NAME",
    help="Environment variable name to delete (Multiple allowed)",
)
@click.argument("name")
def set_env(
    client: ClusterClient,
    name: str,
    updated_vars: dict[str, str],
    deleted_vars: tuple[str],
):
    """Create a new revision of GROUP in CLUSTER with updated values"""
    if dup := "', '".join(set(updated_vars) & set(deleted_vars)):
        raise click.UsageError(f"Cannot update and delete vars at the same time: '{dup}'")

    if not (env := client.envs.get(name)):
        raise click.UsageError(f"Unknown Variable Group '{name}'")

    if missing := "', '".join(set(deleted_vars) - set(env.variables)):
        raise click.UsageError(f"Cannot delete vars not in env: '{missing}'")

    click.echo(
        render_dict_as_yaml(
            env.add_revision(dict(**updated_vars, **{v: None for v in deleted_vars}))
        )
    )


@env.command(name="show")
@click.argument("name")
@click.option("-r", "--revision", type=int, help="Revision of GROUP to show (Defaults to latest)")
def show_env(client: ClusterClient, name: str, revision: int | None):
    """Show all variables in latest revision of GROUP in CLUSTER"""
    if not (env := client.envs.get(name)):
        raise click.UsageError(f"Unknown Variable Group '{name}'")

    for env_info in env.revisions:
        if revision is None or env_info.revision == revision:
            click.echo(render_dict_as_yaml(env_info))
            return

    raise click.UsageError(f"Revision {revision} of '{name}' not found")


@env.command(name="rm")
@click.argument("name")
def remove_env(client: ClusterClient, name: str):
    """
    Remove a variable GROUP from CLUSTER

    NOTE: Cannot delete if any bots reference any revision of GROUP
    """
    if not (env := client.envs.get(name)):
        raise click.UsageError(f"Unknown Variable Group '{name}'")

    env.remove()
    click.secho(f"Variable Group '{env.name}' removed.", fg="green", bold=True)


@cluster.group(cls=OrderedCommands)
def bot():
    """Commands for managing bots in a CLUSTER"""


@bot.command(name="list")
def list_bots(client: ClusterClient):
    """
    List all bots in a CLUSTER

    For clusters on the Silverback Platform, please provide a name for the cluster to access using
    your platform authentication obtained via `silverback login` in `workspace/cluster-name` format

    NOTE: Connecting directly to clusters is supported, but is an advanced use case.
    """
    if bot_display := render_dict_as_yaml(client.bots):
        click.echo(bot_display)

    else:
        click.secho("No bots in this cluster", bold=True, fg="red")


