import asyncio
import os

import click
import yaml  # type: ignore[import-untyped]
from ape.cli import (
    AccountAliasPromptChoice,
    ConnectedProviderCommand,
    ape_cli_context,
    network_option,
)
from ape.exceptions import Abort
from fief_client.integrations.cli import FiefAuth

from silverback._click_ext import (
    AuthCommand,
    PlatformGroup,
    SectionedHelpGroup,
    cls_import_callback,
    display_login_message,
)
from silverback._importer import import_from_string
from silverback.cluster.client import ClusterClient, PlatformClient
from silverback.cluster.types import ClusterTier
from silverback.runner import PollingRunner, WebsocketRunner
from silverback.worker import run_worker


@click.group(cls=SectionedHelpGroup)
def cli():
    """
    Silverback: Build Python apps that react to on-chain events

    To learn more about our cloud offering, please check out https://silverback.apeworx.io
    """


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


@cli.command(
    cls=ConnectedProviderCommand,
    help="Run local Silverback application",
    section="Local Commands",
)
@ape_cli_context()
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


@cli.command(
    cls=ConnectedProviderCommand,
    help="Start Silverback distributed task workers (advanced)",
    section="Local Commands",
)
@ape_cli_context()
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


@cli.command(cls=AuthCommand, section="Cloud Commands (https://silverback.apeworx.io)")
def login(auth: FiefAuth):
    """Login to ApeWorX Authorization Service (https://account.apeworx.io)"""

    auth.authorize()
    display_login_message(auth, auth.client.base_url)


@cli.group(cls=PlatformGroup, section="Cloud Commands (https://silverback.apeworx.io)")
def cluster():
    """Manage a Silverback hosted application cluster

    For clusters on the Silverback Platform, please provide a name for the cluster to access under
    your platform account via `-c WORKSPACE/NAME`"""


@cluster.command(
    section="Platform Commands (https://silverback.apeworx.io)",
    disable_cluster_option=True,
)
def workspaces(client: PlatformClient):
    """List available workspaces for your account"""

    if workspace_names := list(client.workspaces):
        click.echo(yaml.safe_dump(workspace_names))

    else:
        click.secho(
            "No workspaces available for this account. "
            "Go to https://silverback.apeworx.io to sign up and create a new workspace",
            bold=True,
            fg="red",
        )


@cluster.command(
    name="list",
    section="Platform Commands (https://silverback.apeworx.io)",
    disable_cluster_option=True,
)
@click.argument("workspace")
def list_clusters(client: PlatformClient, workspace: str):
    """List available clusters in a WORKSPACE"""

    if not (workspace_client := client.workspaces.get(workspace)):
        raise click.BadOptionUsage("workspace", f"Unknown workspace '{workspace}'")

    if cluster_names := list(workspace_client.clusters):
        click.echo(yaml.safe_dump(cluster_names))

    else:
        click.secho("No clusters for this account", bold=True, fg="red")


@cluster.command(
    name="new",
    section="Platform Commands (https://silverback.apeworx.io)",
    disable_cluster_option=True,
)
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
    """Create a new cluster in WORKSPACE"""

    if not (workspace_client := client.workspaces.get(workspace)):
        raise click.BadOptionUsage("workspace", f"Unknown workspace '{workspace}'")

    configuration = getattr(ClusterTier, tier.upper()).configuration()

    for k, v in config_updates:
        setattr(configuration, k, int(v) if v.isnumeric() else v)

    cluster = workspace_client.create_cluster(
        cluster_name=cluster_name,
        cluster_slug=cluster_slug,
        configuration=configuration,
    )
    click.echo(f"{click.style('SUCCESS', fg='green')}: Created '{cluster.name}'")
    # TODO: Pay for cluster via new stream


# `silverback cluster pay WORKSPACE/NAME --account ALIAS --time "10 days"`
# TODO: Create a signature scheme for ClusterInfo
#         (ClusterInfo configuration as plaintext, .id as nonce?)
# TODO: Test payment w/ Signature validation of extra data


@cluster.command(name="info")
def cluster_info(client: ClusterClient):
    """Get Configuration information about a CLUSTER"""

    # NOTE: This actually doesn't query the cluster's routes, which are protected
    click.echo(f"Cluster Version: v{client.version}")

    if config := client.state.configuration:
        click.echo(yaml.safe_dump(config.model_dump()))

    else:
        click.secho("No Cluster Configuration detected", fg="yellow", bold=True)


@cluster.command(name="health")
def cluster_health(client: ClusterClient):
    """Get Health information about a CLUSTER"""

    click.echo(yaml.safe_dump(client.health.model_dump()))


@cluster.group()
def vars():
    """Manage groups of environment variables in a CLUSTER"""


def parse_envvars(ctx, name, value: list[str]) -> dict[str, str]:
    def parse_envar(item: str):
        if not ("=" in item and len(item.split("=")) == 2):
            raise click.UsageError(f"Value '{item}' must be in form `NAME=VAL`")

        return item.split("=")

    return dict(parse_envar(item) for item in value)


@vars.command(name="new")
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
def new_vargroup(client: ClusterClient, variables: dict, name: str):
    """Create a new group of environment variables in a CLUSTER"""

    if len(variables) == 0:
        raise click.UsageError("Must supply at least one var via `-e`")

    vg = client.new_variable_group(name=name, variables=variables)
    click.echo(yaml.safe_dump(vg.model_dump(exclude={"id"})))  # NOTE: Skip machine `.id`


@vars.command(name="list")
def list_vargroups(client: ClusterClient):
    """List latest revisions of all variable groups in a CLUSTER"""

    if group_names := list(client.variable_groups):
        click.echo(yaml.safe_dump(group_names))

    else:
        click.secho("No Variable Groups present in this cluster", bold=True, fg="red")


@vars.command(name="info")
@click.argument("name")
def vargroup_info(client: ClusterClient, name: str):
    """Show latest revision of a variable GROUP in a CLUSTER"""

    if not (vg := client.variable_groups.get(name)):
        raise click.UsageError(f"Unknown Variable Group '{name}'")

    click.echo(yaml.safe_dump(vg.model_dump(exclude={"id", "name"})))


@vars.command(name="update")
@click.option("--new-name", "new_name")  # NOTE: No `-n` to match `bots update`
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
def update_vargroup(
    client: ClusterClient,
    name: str,
    new_name: str,
    updated_vars: dict[str, str],
    deleted_vars: tuple[str],
):
    """Update a variable GROUP in CLUSTER

    NOTE: Changing the values of variables in GROUP by create a new revision, since variable groups
    are immutable. New revisions do not automatically update bot configuration."""

    if not (vg := client.variable_groups.get(name)):
        raise click.UsageError(f"Unknown Variable Group '{name}'")

    if dup := "', '".join(set(updated_vars) & set(deleted_vars)):
        raise click.UsageError(f"Cannot update and delete vars at the same time: '{dup}'")

    if missing := "', '".join(set(deleted_vars) - set(vg.variables)):
        raise click.UsageError(f"Cannot delete vars not in group: '{missing}'")

    click.echo(
        yaml.safe_dump(
            vg.update(
                name=new_name,
                # NOTE: Do not update variables if no updates are provided
                variables=dict(**updated_vars, **{v: None for v in deleted_vars}) or None,
            ).model_dump(
                exclude={"id"}
            )  # NOTE: Skip machine `.id`
        )
    )


@vars.command(name="remove")
@click.argument("name")
def remove_vargroup(client: ClusterClient, name: str):
    """
    Remove a variable GROUP from a CLUSTER

    NOTE: Cannot delete if any bots reference any revision of GROUP
    """
    if not (vg := client.variable_groups.get(name)):
        raise click.UsageError(f"Unknown Variable Group '{name}'")

    vg.remove()  # NOTE: No confirmation because can only delete if no references exist
    click.secho(f"Variable Group '{vg.name}' removed.", fg="green", bold=True)


@cluster.group()
def bots():
    """Manage bots in a CLUSTER"""


@bots.command(name="new", section="Configuration Commands")
@click.option("-i", "--image", required=True)
@click.option("-n", "--network", required=True)
@click.option("-a", "--account")
@click.option("-g", "--group", "groups", multiple=True)
@click.argument("name")
def new_bot(
    client: ClusterClient,
    image: str,
    network: str,
    account: str | None,
    groups: list[str],
    name: str,
):
    """Create a new bot in a CLUSTER with the given configuration"""

    if name in client.bots:
        raise click.UsageError(f"Cannot use name '{name}' to create bot")

    environment = list()
    for vg_id in groups:
        if "/" in vg_id:
            vg_name, revision = vg_id.split("/")
            vg = client.variable_groups[vg_name].get_revision(int(revision))

        else:
            vg = client.variable_groups[vg_id]

        environment.append(vg)

    click.echo(f"Name: {name}")
    click.echo(f"Image: {image}")
    click.echo(f"Network: {network}")
    if environment:
        click.echo("Environment:")
        click.echo(yaml.safe_dump([var for vg in environment for var in vg.variables]))

    if not click.confirm("Do you want to create this bot?"):
        bot = client.new_bot(name, image, network, account=account, environment=environment)
        click.secho(f"Bot '{bot.name}' ({bot.id}) deploying...", fg="green", bold=True)


@bots.command(name="list", section="Configuration Commands")
def list_bots(client: ClusterClient):
    """List all bots in a CLUSTER (Regardless of status)"""

    if bot_names := list(client.bots):
        click.echo(yaml.safe_dump(bot_names))

    else:
        click.secho("No bots in this cluster", bold=True, fg="red")


@bots.command(name="info", section="Configuration Commands")
@click.argument("bot_name", metavar="BOT")
def bot_info(client: ClusterClient, bot_name: str):
    """Get configuration information of a BOT in a CLUSTER"""

    if not (bot := client.bots.get(bot_name)):
        raise click.UsageError(f"Unknown bot '{bot_name}'.")

    # NOTE: Skip machine `.id`, and we already know it is `.name`
    click.echo(yaml.safe_dump(bot.model_dump(exclude={"id", "name", "environment"})))
    if bot.environment:
        click.echo("environment:")
        click.echo(yaml.safe_dump([var.name for var in bot.environment]))


@bots.command(name="update", section="Configuration Commands")
@click.option("--new-name", "new_name")  # NOTE: No shorthand, because conflicts w/ `--network`
@click.option("-i", "--image")
@click.option("-n", "--network")
@click.option("-a", "--account")
@click.option("-g", "--group", "groups", multiple=True)
@click.argument("name", metavar="BOT")
def update_bot(
    client: ClusterClient,
    new_name: str | None,
    image: str | None,
    network: str | None,
    account: str | None,
    groups: list[str],
    name: str,
):
    """Update configuration of BOT in CLUSTER

    NOTE: Some configuration updates will trigger a redeploy"""

    if new_name in client.bots:
        raise click.UsageError(f"Cannot use name '{new_name}' to update bot '{name}'")

    if not (bot := client.bots.get(name)):
        raise click.UsageError(f"Unknown bot '{name}'.")

    if new_name:
        click.echo(f"Name:\n  old: {name}\n  new: {new_name}")

    if network:
        click.echo(f"Network:\n  old: {bot.network}\n  new: {network}")

    redeploy_required = False
    if image:
        redeploy_required = True
        click.echo(f"Image:\n  old: {bot.image}\n  new: {image}")

    environment = list()
    for vg_id in groups:
        if "/" in vg_id:
            vg_name, revision = vg_id.split("/")
            vg = client.variable_groups[vg_name].get_revision(int(revision))

        else:
            vg = client.variable_groups[vg_id]

        environment.append(vg)

    set_environment = True

    if len(environment) == 0 and bot.environment:
        set_environment = click.confirm("Do you want to clear all environment variables?")

    elif environment != bot.environment:
        click.echo("old-environment:")
        click.echo(yaml.safe_dump([var.name for var in bot.environment]))
        click.echo("new-environment:")
        click.echo(yaml.safe_dump([var for vg in environment for var in vg.variables]))

    redeploy_required |= set_environment

    if click.confirm(
        f"Do you want to update '{name}'?"
        if not redeploy_required
        else f"Do you want to update and redeploy '{name}'?"
    ):
        bot = bot.update(
            name=new_name,
            image=image,
            network=network,
            account=account,
            environment=environment if set_environment else None,
        )
        # NOTE: Skip machine `.id`
        click.echo(yaml.safe_dump(bot.model_dump(exclude={"id", "environment"})))
        if bot.environment:
            click.echo("environment:")
            click.echo(yaml.safe_dump([var.name for var in bot.environment]))


@bots.command(name="remove", section="Configuration Commands")
@click.argument("name", metavar="BOT")
def remove_bot(client: ClusterClient, name: str):
    """Remove BOT from CLUSTER (Shutdown if running)"""

    if not (bot := client.bots.get(name)):
        raise click.UsageError(f"Unknown bot '{name}'.")

    elif click.confirm(f"Do you want to shutdown and delete '{name}'?"):
        bot.remove()
        click.secho(f"Bot '{bot.name}' removed.", fg="green", bold=True)


@bots.command(name="health", section="Bot Operation Commands")
@click.argument("bot_name", metavar="BOT")
def bot_health(client: ClusterClient, bot_name: str):
    """Show current health of BOT in a CLUSTER"""

    if not (bot := client.bots.get(bot_name)):
        raise click.UsageError(f"Unknown bot '{bot_name}'.")

    click.echo(yaml.safe_dump(bot.health.model_dump(exclude={"bot_id"})))


@bots.command(name="start", section="Bot Operation Commands")
@click.argument("name", metavar="BOT")
def start_bot(client: ClusterClient, name: str):
    """Start BOT running in CLUSTER (if stopped or terminated)"""

    if not (bot := client.bots.get(name)):
        raise click.UsageError(f"Unknown bot '{name}'.")

    elif click.confirm(f"Do you want to start running '{name}'?"):
        bot.start()
        click.secho(f"Bot '{bot.name}' starting...", fg="green", bold=True)


@bots.command(name="stop", section="Bot Operation Commands")
@click.argument("name", metavar="BOT")
def stop_bot(client: ClusterClient, name: str):
    """Stop BOT from running in CLUSTER (if running)"""

    if not (bot := client.bots.get(name)):
        raise click.UsageError(f"Unknown bot '{name}'.")

    elif click.confirm(f"Do you want to stop '{name}' from running?"):
        bot.stop()
        click.secho(f"Bot '{bot.name}' stopping...", fg="green", bold=True)


@bots.command(name="logs", section="Bot Operation Commands")
@click.argument("name", metavar="BOT")
def show_bot_logs(client: ClusterClient, name: str):
    """Show runtime logs for BOT in CLUSTER"""

    if not (bot := client.bots.get(name)):
        raise click.UsageError(f"Unknown bot '{name}'.")

    for log in bot.logs:
        click.echo(log)


@bots.command(name="errors", section="Bot Operation Commands")
@click.argument("name", metavar="BOT")
def show_bot_errors(client: ClusterClient, name: str):
    """Show unacknowledged errors for BOT in CLUSTER"""

    if not (bot := client.bots.get(name)):
        raise click.UsageError(f"Unknown bot '{name}'.")

    for log in bot.errors:
        click.echo(log)
