import asyncio
import os
from pathlib import Path

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
    SectionedHelpGroup,
    auth_required,
    cls_import_callback,
    cluster_client,
    display_login_message,
    platform_client,
)
from silverback._importer import import_from_string
from silverback.cluster.client import ClusterClient, PlatformClient
from silverback.cluster.types import ClusterTier
from silverback.runner import PollingRunner, WebsocketRunner
from silverback.worker import run_worker

DOCKERFILE_CONTENT = """
FROM ghcr.io/apeworx/silverback:latest-slim
USER root
WORKDIR /app
RUN mkdir -p ./bots && chown harambe:harambe /app/bots
USER harambe
COPY ape-config.yaml .
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt
RUN ape plugins install .
"""


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


@cli.command(cls=ConnectedProviderCommand, section="Local Commands")
@ape_cli_context()
@network_option(
    default=os.environ.get("SILVERBACK_NETWORK_CHOICE", "auto"),
    callback=_network_callback,
)
@click.option("--account", type=AccountAliasPromptChoice(), callback=_account_callback)
@click.option(
    "--runner",
    "runner_class",
    metavar="CLASS_REF",
    help="An import str in format '<module>:<CustomRunner>'",
    callback=cls_import_callback,
)
@click.option(
    "--recorder",
    "recorder_class",
    metavar="CLASS_REF",
    help="An import string in format '<module>:<CustomRecorder>'",
    callback=cls_import_callback,
)
@click.option("-x", "--max-exceptions", type=int, default=3)
@click.argument("path", required=False, type=str, default="bot")
def run(cli_ctx, account, runner_class, recorder_class, max_exceptions, path):
    """Run Silverback application"""

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

    if not (bots_folder := Path.cwd() / "bots").exists():
        raise FileNotFoundError(
            f"The bots directory '{path}' does not exist. "
            f"You should have a `bots/` folder in the root of your project."
        )

    elif not (bots_folder / f"{path}.py").exists():
        raise FileNotFoundError(f"The file '{path}.py' does not exist in the `bots/` directory.")

    else:
        path = f"bots.{path}:bot"

    if ":" not in path:
        path += ":bot"

    app = import_from_string(path)
    runner = runner_class(
        app,
        recorder=recorder_class() if recorder_class else None,
        max_exceptions=max_exceptions,
    )
    asyncio.run(runner.run())


@cli.command(cls=ConnectedProviderCommand, section="Local Commands")
@click.argument("path", required=False, type=str, default="bots")
def generate_dockerfiles(path):
    if not (path := Path.cwd() / path).exists():
        raise FileNotFoundError(
            f"The bots directory '{path}' does not exist. "
            "You should have a `{path}/` folder in the root of your project."
        )
    files = {file for file in path.iterdir() if file.is_file()}
    bots = []
    for file in files:
        if "__init__" in file.name:
            if not click.confirm(
                "There is an __init__.py file in the bots directory,\n"
                f"making the {path}/ directory a python package.\n"
                "Are you sure you want to generate a Dockerfile for all "
                "files in this directory?"
            ):
                return
            continue
        bots.append(file)
    for bot in bots:
        docker_filename = f"Dockerfile.{bot.name.replace('.py', '')}"
        dockerfile_content = DOCKERFILE_CONTENT
        dockerfile_content += f"COPY {path.name}/{bot.name} bots/"
        with open(dockerfile_path := Path.cwd() / docker_filename, "w") as df:
            df.write(dockerfile_content.strip() + "\n")
        click.echo(f"Generated {dockerfile_path}")


@cli.command(cls=ConnectedProviderCommand, section="Local Commands")
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
    """Run Silverback task workers (advanced)"""

    app = import_from_string(path)
    asyncio.run(run_worker(app.broker, worker_count=workers, shutdown_timeout=shutdown_timeout))


@cli.command(section="Cloud Commands (https://silverback.apeworx.io)")
@auth_required
def login(auth: FiefAuth):
    """Login to ApeWorX Authorization Service (https://account.apeworx.io)"""

    auth.authorize()
    display_login_message(auth, auth.client.base_url)


@cli.group(cls=SectionedHelpGroup, section="Cloud Commands (https://silverback.apeworx.io)")
def cluster():
    """Manage a Silverback hosted application cluster

    For clusters on the Silverback Platform, please provide a name for the cluster to access under
    your platform account via `-c WORKSPACE/NAME`"""


@cluster.command(section="Platform Commands (https://silverback.apeworx.io)")
@platform_client
def workspaces(platform: PlatformClient):
    """List available workspaces for your account"""

    if workspace_names := list(platform.workspaces):
        click.echo(yaml.safe_dump(workspace_names))

    else:
        click.secho(
            "No workspaces available for this account. "
            "Go to https://silverback.apeworx.io to sign up and create a new workspace",
            bold=True,
            fg="red",
        )


@cluster.command(name="list", section="Platform Commands (https://silverback.apeworx.io)")
@click.argument("workspace")
@platform_client
def list_clusters(platform: PlatformClient, workspace: str):
    """List available clusters in a WORKSPACE"""

    if not (workspace_client := platform.workspaces.get(workspace)):
        raise click.BadOptionUsage("workspace", f"Unknown workspace '{workspace}'")

    if cluster_names := list(workspace_client.clusters):
        click.echo(yaml.safe_dump(cluster_names))

    else:
        click.secho("No clusters for this account", bold=True, fg="red")


@cluster.command(name="new", section="Platform Commands (https://silverback.apeworx.io)")
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
    help="Slug for new cluster (Defaults to `name.lower()`)",
)
@click.option(
    "-t",
    "--tier",
    default=ClusterTier.PERSONAL.name,
    metavar="NAME",
    help="Named set of options to use for cluster as a base (Defaults to Personal)",
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
@platform_client
def new_cluster(
    platform: PlatformClient,
    workspace: str,
    cluster_name: str | None,
    cluster_slug: str | None,
    tier: str,
    config_updates: list[tuple[str, str]],
):
    """Create a new cluster in WORKSPACE"""

    if not (workspace_client := platform.workspaces.get(workspace)):
        raise click.BadOptionUsage("workspace", f"Unknown workspace '{workspace}'")

    if not hasattr(ClusterTier, tier.upper()):
        raise click.BadOptionUsage("tier", f"Invalid choice: {tier}")

    configuration = getattr(ClusterTier, tier.upper()).configuration()

    for k, v in config_updates:
        setattr(configuration, k, int(v) if v.isnumeric() else v)

    if cluster_name:
        click.echo(f"name: {cluster_name}")
        click.echo(f"slug: {cluster_slug or cluster_name.lower().replace(' ', '-')}")

    elif cluster_slug:
        click.echo(f"slug: {cluster_slug}")

    click.echo(yaml.safe_dump(dict(configuration=configuration.settings_display_dict())))

    if not click.confirm("Do you want to make a new cluster with this configuration?"):
        return

    cluster = workspace_client.create_cluster(
        cluster_name=cluster_name,
        cluster_slug=cluster_slug,
    )
    click.echo(f"{click.style('SUCCESS', fg='green')}: Created '{cluster.name}'")
    # TODO: Pay for cluster via new stream


# `silverback cluster pay WORKSPACE/NAME --account ALIAS --time "10 days"`
# TODO: Create a signature scheme for ClusterInfo
#         (ClusterInfo configuration as plaintext, .id as nonce?)
# TODO: Test payment w/ Signature validation of extra data


@cluster.command(name="info")
@cluster_client
def cluster_info(cluster: ClusterClient):
    """Get Configuration information about a CLUSTER"""

    # NOTE: This actually doesn't query the cluster's routes, which are protected
    click.echo(f"Cluster Version: v{cluster.version}")

    if config := cluster.state.configuration:
        click.echo(yaml.safe_dump(config.settings_display_dict()))

    else:
        click.secho("No Cluster Configuration detected", fg="yellow", bold=True)


@cluster.command(name="health")
@cluster_client
def cluster_health(cluster: ClusterClient):
    """Get Health information about a CLUSTER"""

    click.echo(yaml.safe_dump(cluster.health.model_dump()))


@cluster.group(cls=SectionedHelpGroup)
def registry():
    """Manage container registry configuration"""


@registry.group(cls=SectionedHelpGroup, name="auth")
def registry_auth():
    """Manage private container registry credentials"""


@registry_auth.command(name="list")
@cluster_client
def credentials_list(cluster: ClusterClient):
    """List container registry credentials"""

    if creds := list(cluster.registry_credentials):
        click.echo(yaml.safe_dump(creds))

    else:
        click.secho("No registry credentials present in this cluster", bold=True, fg="red")


@registry_auth.command(name="info")
@click.argument("name")
@cluster_client
def credentials_info(cluster: ClusterClient, name: str):
    """Show info about registry credentials"""

    if not (creds := cluster.registry_credentials.get(name)):
        raise click.UsageError(f"Unknown credentials '{name}'")

    click.echo(yaml.safe_dump(creds.model_dump(exclude={"id", "name"})))


@registry_auth.command(name="new")
@click.argument("name")
@click.argument("registry")
@cluster_client
def credentials_new(cluster: ClusterClient, name: str, registry: str):
    """Add registry private registry credentials. This command will prompt you for a username and
    password.
    """

    username = click.prompt("Username")
    password = click.prompt("Password", hide_input=True)

    creds = cluster.new_credentials(
        name=name, hostname=registry, username=username, password=password
    )
    click.echo(yaml.safe_dump(creds.model_dump(exclude={"id"})))


@registry_auth.command(name="update")
@click.argument("name")
@click.option("-r", "--registry")
@cluster_client
def credentials_update(cluster: ClusterClient, name: str, registry: str | None = None):
    """Update registry registry credentials"""
    if not (creds := cluster.registry_credentials.get(name)):
        raise click.UsageError(f"Unknown credentials '{name}'")

    username = click.prompt("Username")
    password = click.prompt("Password", hide_input=True)

    creds = creds.update(hostname=registry, username=username, password=password)
    click.echo(yaml.safe_dump(creds.model_dump(exclude={"id"})))


@registry_auth.command(name="remove")
@click.argument("name")
@cluster_client
def credentials_remove(cluster: ClusterClient, name: str):
    """Remove a set of registry credentials"""
    if not (creds := cluster.registry_credentials.get(name)):
        raise click.UsageError(f"Unknown credentials '{name}'")

    creds.remove()  # NOTE: No confirmation because can only delete if no references exist
    click.secho(f"registry credentials '{creds.name}' removed.", fg="green", bold=True)


@cluster.group(cls=SectionedHelpGroup)
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
@cluster_client
def new_vargroup(cluster: ClusterClient, variables: dict, name: str):
    """Create a new group of environment variables in a CLUSTER"""

    if len(variables) == 0:
        raise click.UsageError("Must supply at least one var via `-e`")

    vg = cluster.new_variable_group(name=name, variables=variables)
    click.echo(yaml.safe_dump(vg.model_dump(exclude={"id"})))  # NOTE: Skip machine `.id`


@vars.command(name="list")
@cluster_client
def list_vargroups(cluster: ClusterClient):
    """List latest revisions of all variable groups in a CLUSTER"""

    if group_names := list(cluster.variable_groups):
        click.echo(yaml.safe_dump(group_names))

    else:
        click.secho("No Variable Groups present in this cluster", bold=True, fg="red")


@vars.command(name="info")
@click.argument("name")
@cluster_client
def vargroup_info(cluster: ClusterClient, name: str):
    """Show latest revision of a variable GROUP in a CLUSTER"""

    if not (vg := cluster.variable_groups.get(name)):
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
@cluster_client
def update_vargroup(
    cluster: ClusterClient,
    name: str,
    new_name: str,
    updated_vars: dict[str, str],
    deleted_vars: tuple[str],
):
    """Update a variable GROUP in CLUSTER

    NOTE: Changing the values of variables in GROUP by create a new revision, since variable groups
    are immutable. New revisions do not automatically update bot configuration."""

    if not (vg := cluster.variable_groups.get(name)):
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
@cluster_client
def remove_vargroup(cluster: ClusterClient, name: str):
    """
    Remove a variable GROUP from a CLUSTER

    NOTE: Cannot delete if any bots reference any revision of GROUP
    """
    if not (vg := cluster.variable_groups.get(name)):
        raise click.UsageError(f"Unknown Variable Group '{name}'")

    vg.remove()  # NOTE: No confirmation because can only delete if no references exist
    click.secho(f"Variable Group '{vg.name}' removed.", fg="green", bold=True)


@cluster.group(cls=SectionedHelpGroup)
def bots():
    """Manage bots in a CLUSTER"""


@bots.command(name="new", section="Configuration Commands")
@click.option("-i", "--image", required=True)
@click.option("-n", "--network", required=True)
@click.option("-a", "--account")
@click.option("-g", "--group", "vargroups", multiple=True)
@click.option(
    "-r",
    "--registry-credentials",
    "registry_credentials_name",
    help="registry credentials to use to pull the image",
)
@click.argument("name")
@cluster_client
def new_bot(
    cluster: ClusterClient,
    image: str,
    network: str,
    account: str | None,
    vargroups: list[str],
    registry_credentials_name: str | None,
    name: str,
):
    """Create a new bot in a CLUSTER with the given configuration"""

    if name in cluster.bots:
        raise click.UsageError(f"Cannot use name '{name}' to create bot")

    environment = [cluster.variable_groups[vg_name].get_revision("latest") for vg_name in vargroups]

    registry_credentials_id = None
    if registry_credentials_name:
        if not (
            creds := cluster.registry_credentials.get(registry_credentials_name)
        ):  # NOTE: Check if credentials exist
            raise click.UsageError(f"Unknown registry credentials '{registry_credentials_name}'")
        registry_credentials_id = creds.id

    click.echo(f"Name: {name}")
    click.echo(f"Image: {image}")
    click.echo(f"Network: {network}")
    if environment:
        click.echo("Environment:")
        click.echo(yaml.safe_dump([var for vg in environment for var in vg.variables]))
    if registry_credentials_id:
        click.echo(f"registry credentials: {registry_credentials_name}")

    if not click.confirm("Do you want to create and start running this bot?"):
        return

    bot = cluster.new_bot(
        name,
        image,
        network,
        account=account,
        environment=environment,
        registry_credentials_id=registry_credentials_id,
    )
    click.secho(f"Bot '{bot.name}' ({bot.id}) deploying...", fg="green", bold=True)


@bots.command(name="list", section="Configuration Commands")
@cluster_client
def list_bots(cluster: ClusterClient):
    """List all bots in a CLUSTER (Regardless of status)"""

    if bot_names := list(cluster.bots):
        click.echo(yaml.safe_dump(bot_names))

    else:
        click.secho("No bots in this cluster", bold=True, fg="red")


@bots.command(name="info", section="Configuration Commands")
@click.argument("bot_name", metavar="BOT")
@cluster_client
def bot_info(cluster: ClusterClient, bot_name: str):
    """Get configuration information of a BOT in a CLUSTER"""

    if not (bot := cluster.bots.get(bot_name)):
        raise click.UsageError(f"Unknown bot '{bot_name}'.")

    # NOTE: Skip machine `.id`, and we already know it is `.name`
    bot_dump = bot.model_dump(
        exclude={
            "id",
            "name",
            "environment",
            "registry_credentials_id",
            "registry_credentials",
        }
    )
    if bot.registry_credentials:
        bot_dump["registry_credentials"] = bot.registry_credentials.model_dump(
            exclude={"id", "name"}
        )

    click.echo(yaml.safe_dump(bot_dump))
    if bot.environment:
        click.echo("environment:")
        click.echo(yaml.safe_dump([var.name for var in bot.environment]))


@bots.command(name="update", section="Configuration Commands")
@click.option("--new-name", "new_name")  # NOTE: No shorthand, because conflicts w/ `--network`
@click.option("-i", "--image")
@click.option("-n", "--network")
@click.option("-a", "--account")
@click.option("-g", "--group", "vargroups", multiple=True)
@click.option(
    "-r",
    "--registry-credentials",
    "registry_credentials_name",
    help="registry credentials to use to pull the image",
)
@click.argument("name", metavar="BOT")
@cluster_client
def update_bot(
    cluster: ClusterClient,
    new_name: str | None,
    image: str | None,
    network: str | None,
    account: str | None,
    vargroups: list[str],
    registry_credentials_name: str | None,
    name: str,
):
    """Update configuration of BOT in CLUSTER

    NOTE: Some configuration updates will trigger a redeploy"""

    if new_name in cluster.bots:
        raise click.UsageError(f"Cannot use name '{new_name}' to update bot '{name}'")

    if not (bot := cluster.bots.get(name)):
        raise click.UsageError(f"Unknown bot '{name}'.")

    if new_name:
        click.echo(f"Name:\n  old: {name}\n  new: {new_name}")

    if network:
        click.echo(f"Network:\n  old: {bot.network}\n  new: {network}")

    registry_credentials_id = None
    if registry_credentials_name:
        if not (
            creds := cluster.registry_credentials.get(registry_credentials_name)
        ):  # NOTE: Check if credentials exist
            raise click.UsageError(f"Unknown registry credentials '{registry_credentials_name}'")
        registry_credentials_id = creds.id

    redeploy_required = False
    if image:
        redeploy_required = True
        click.echo(f"Image:\n  old: {bot.image}\n  new: {image}")

    environment = [cluster.variable_groups[vg_name].get_revision("latest") for vg_name in vargroups]

    set_environment = True

    if len(environment) == 0 and bot.environment:
        set_environment = click.confirm("Do you want to clear all environment variables?")

    elif environment != bot.environment:
        click.echo("old-environment:")
        click.echo(yaml.safe_dump([var.name for var in bot.environment]))
        click.echo("new-environment:")
        click.echo(yaml.safe_dump([var for vg in environment for var in vg.variables]))

    redeploy_required |= set_environment

    if not click.confirm(
        f"Do you want to update '{name}'?"
        if not redeploy_required
        else f"Do you want to update and redeploy '{name}'?"
    ):
        return

    bot = bot.update(
        name=new_name,
        image=image,
        network=network,
        account=account,
        environment=environment if set_environment else None,
        registry_credentials_id=registry_credentials_id,
    )

    # NOTE: Skip machine `.id`
    click.echo(yaml.safe_dump(bot.model_dump(exclude={"id", "environment"})))
    if bot.environment:
        click.echo("environment:")
        click.echo(yaml.safe_dump([var.name for var in bot.environment]))


@bots.command(name="remove", section="Configuration Commands")
@click.argument("name", metavar="BOT")
@cluster_client
def remove_bot(cluster: ClusterClient, name: str):
    """Remove BOT from CLUSTER (Shutdown if running)"""

    if not (bot := cluster.bots.get(name)):
        raise click.UsageError(f"Unknown bot '{name}'.")

    elif not click.confirm(f"Do you want to shutdown and delete '{name}'?"):
        return

    bot.remove()
    click.secho(f"Bot '{bot.name}' removed.", fg="green", bold=True)


@bots.command(name="health", section="Bot Operation Commands")
@click.argument("bot_name", metavar="BOT")
@cluster_client
def bot_health(cluster: ClusterClient, bot_name: str):
    """Show current health of BOT in a CLUSTER"""

    if not (bot := cluster.bots.get(bot_name)):
        raise click.UsageError(f"Unknown bot '{bot_name}'.")

    click.echo(yaml.safe_dump(bot.health.model_dump(exclude={"bot_id"})))


@bots.command(name="start", section="Bot Operation Commands")
@click.argument("name", metavar="BOT")
@cluster_client
def start_bot(cluster: ClusterClient, name: str):
    """Start BOT running in CLUSTER (if stopped or terminated)"""

    if not (bot := cluster.bots.get(name)):
        raise click.UsageError(f"Unknown bot '{name}'.")

    elif not click.confirm(f"Do you want to start running '{name}'?"):
        return

    bot.start()
    click.secho(f"Bot '{bot.name}' starting...", fg="green", bold=True)


@bots.command(name="stop", section="Bot Operation Commands")
@click.argument("name", metavar="BOT")
@cluster_client
def stop_bot(cluster: ClusterClient, name: str):
    """Stop BOT from running in CLUSTER (if running)"""

    if not (bot := cluster.bots.get(name)):
        raise click.UsageError(f"Unknown bot '{name}'.")

    elif not click.confirm(f"Do you want to stop '{name}' from running?"):
        return

    bot.stop()
    click.secho(f"Bot '{bot.name}' stopping...", fg="green", bold=True)


@bots.command(name="logs", section="Bot Operation Commands")
@click.argument("name", metavar="BOT")
@cluster_client
def show_bot_logs(cluster: ClusterClient, name: str):
    """Show runtime logs for BOT in CLUSTER"""

    if not (bot := cluster.bots.get(name)):
        raise click.UsageError(f"Unknown bot '{name}'.")

    for log in bot.logs:
        click.echo(log)


@bots.command(name="errors", section="Bot Operation Commands")
@click.argument("name", metavar="BOT")
@cluster_client
def show_bot_errors(cluster: ClusterClient, name: str):
    """Show unacknowledged errors for BOT in CLUSTER"""

    if not (bot := cluster.bots.get(name)):
        raise click.UsageError(f"Unknown bot '{name}'.")

    for log in bot.errors:
        click.echo(log)
