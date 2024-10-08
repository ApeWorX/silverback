import asyncio
import os
from datetime import timedelta

import click
import yaml  # type: ignore[import-untyped]
from ape.api import AccountAPI, NetworkAPI
from ape.cli import (
    AccountAliasPromptChoice,
    ConnectedProviderCommand,
    account_option,
    ape_cli_context,
    network_option,
)
from ape.contracts import ContractInstance
from ape.exceptions import Abort, ApeException
from fief_client.integrations.cli import FiefAuth

from silverback._click_ext import (
    SectionedHelpGroup,
    auth_required,
    cls_import_callback,
    cluster_client,
    display_login_message,
    platform_client,
    timedelta_callback,
    token_amount_callback,
)
from silverback._importer import import_from_string
from silverback.cluster.client import ClusterClient, PlatformClient
from silverback.cluster.types import ClusterTier, ResourceStatus
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
@click.argument("path")
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

    app = import_from_string(path)
    runner = runner_class(
        app,
        recorder=recorder_class() if recorder_class else None,
        max_exceptions=max_exceptions,
    )
    asyncio.run(runner.run())


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
@click.argument("workspace")
@platform_client
def new_cluster(
    platform: PlatformClient,
    workspace: str,
    cluster_name: str | None,
    cluster_slug: str | None,
):
    """Create a new cluster in WORKSPACE"""

    if not (workspace_client := platform.workspaces.get(workspace)):
        raise click.BadOptionUsage("workspace", f"Unknown workspace '{workspace}'")

    if cluster_name:
        click.echo(f"name: {cluster_name}")
        click.echo(f"slug: {cluster_slug or cluster_name.lower().replace(' ', '-')}")

    elif cluster_slug:
        click.echo(f"slug: {cluster_slug}")

    cluster = workspace_client.create_cluster(
        cluster_name=cluster_name,
        cluster_slug=cluster_slug,
    )
    click.echo(f"{click.style('SUCCESS', fg='green')}: Created '{cluster.name}'")

    if cluster.status == ResourceStatus.CREATED:
        click.echo(
            f"{click.style('WARNING', fg='yellow')}: To use this cluster, "
            f"please pay via `silverback cluster pay create {workspace}/{cluster_slug}`"
        )


@cluster.group(cls=SectionedHelpGroup, section="Platform Commands (https://silverback.apeworx.io)")
def pay():
    """Pay for CLUSTER with Crypto using ApePay streaming payments"""


@pay.command(name="create", cls=ConnectedProviderCommand)
@account_option()
@click.argument("cluster_path")
@click.option(
    "-t",
    "--tier",
    default=ClusterTier.PERSONAL.name.capitalize(),
    metavar="NAME",
    type=click.Choice(
        [
            ClusterTier.PERSONAL.name.capitalize(),
            ClusterTier.PROFESSIONAL.name.capitalize(),
        ]
    ),
    help="Named set of options to use for cluster as a base (Defaults to Personal)",
)
@click.option(
    "-c",
    "--config",
    "config_updates",
    metavar="KEY VALUE",
    type=(str, str),
    multiple=True,
    help="Config options to set for cluster (overrides values from -t/--tier selection)",
)
@click.option("--token", metavar="ADDRESS", help="Token Symbol or Address to use to fund stream")
@click.option(
    "--amount",
    "token_amount",
    metavar="VALUE",
    callback=token_amount_callback,
    default=None,
    help="Token amount to use to fund stream",
)
@click.option(
    "--time",
    "stream_time",
    metavar="TIMESTAMP or TIMEDELTA",
    callback=timedelta_callback,
    default=None,
    help="Time to fund stream for",
)
@platform_client
def create_payment_stream(
    platform: PlatformClient,
    network: NetworkAPI,
    account: AccountAPI,
    cluster_path: str,
    tier: str,
    config_updates: list[tuple[str, str]],
    token: ContractInstance | None,
    token_amount: int | None,
    stream_time: timedelta | None,
):
    """
    Create a new streaming payment for a given CLUSTER

    NOTE: This action is irreversible!
    """

    if "/" not in cluster_path or len(cluster_path.split("/")) > 2:
        raise click.BadArgumentUsage(f"Invalid cluster path: '{cluster_path}'")

    workspace_name, cluster_name = cluster_path.split("/")
    if not (workspace_client := platform.workspaces.get(workspace_name)):
        raise click.BadArgumentUsage(f"Unknown workspace: '{workspace_name}'")

    elif not (cluster := workspace_client.clusters.get(cluster_name)):
        raise click.BadArgumentUsage(
            f"Unknown cluster in workspace '{workspace_name}': '{cluster_name}'"
        )

    elif cluster.status != ResourceStatus.CREATED:
        raise click.UsageError(f"Cannot fund '{cluster_path}': cluster has existing streams.")

    elif token_amount is None and stream_time is None:
        raise click.UsageError("Must specify one of '--amount' or '--time'.")

    if not hasattr(ClusterTier, tier.upper()):
        raise click.BadOptionUsage("tier", f"Invalid choice: {tier}")

    configuration = getattr(ClusterTier, tier.upper()).configuration()

    for k, v in config_updates:
        setattr(configuration, k, int(v) if v.isnumeric() else v)

    sm = platform.get_stream_manager(network.chain_id)
    product = configuration.get_product_code(account.address, cluster.id)

    if not token:
        accepted_tokens = platform.get_accepted_tokens(network.chain_id)
        token = accepted_tokens.get(
            click.prompt(
                "Select one of the following tokens to fund your stream with",
                type=click.Choice(list(accepted_tokens)),
            )
        )
    assert token  # mypy happy

    if not token_amount:
        assert stream_time  # mypy happy
        one_token = 10 ** token.decimals()
        token_amount = int(
            one_token
            * (
                stream_time.total_seconds()
                / sm.compute_stream_life(
                    account.address, token, one_token, [product]
                ).total_seconds()
            )
        )
    else:
        stream_time = sm.compute_stream_life(account.address, token, token_amount, [product])

    assert token_amount  # mypy happy

    click.echo(yaml.safe_dump(dict(configuration=configuration.settings_display_dict())))
    click.echo(f"duration: {stream_time}\n")

    if not click.confirm(
        f"Do you want to use this configuration to fund Cluster '{cluster_path}'?"
    ):
        return

    if not token.balanceOf(account) >= token_amount:
        raise click.UsageError(
            f"Do not have sufficient balance of '{token.symbol()}' to fund stream."
        )

    elif not token.allowance(account, sm.address) >= token_amount:
        click.echo(f"Approve StreamManager({sm.address}) for '{token.symbol()}'")
        token.approve(
            sm.address,
            2**256 - 1 if click.confirm("Unlimited Approval?") else token_amount,
            sender=account,
        )

    # NOTE: will ask for approvals and do additional checks
    try:
        stream = sm.create(
            token, token_amount, [product], min_stream_life=stream_time, sender=account
        )
    except ApeException as e:
        raise click.UsageError(str(e)) from e

    click.echo(f"{click.style('SUCCESS', fg='green')}: Cluster funded for {stream.time_left}.")

    click.echo(
        f"{click.style('WARNING', fg='yellow')}: Cluster may take up to 1 hour to deploy."
        " Check back in 10-15 minutes using `silverback cluster info` to start using your cluster."
    )


@pay.command(name="add-time", cls=ConnectedProviderCommand)
@account_option()
@click.argument("cluster_path", metavar="CLUSTER")
@click.option(
    "--amount",
    "token_amount",
    metavar="VALUE",
    callback=token_amount_callback,
    default=None,
    help="Token amount to use to fund stream",
)
@click.option(
    "--time",
    "stream_time",
    metavar="TIMESTAMP or TIMEDELTA",
    callback=timedelta_callback,
    default=None,
    help="Time to fund stream for",
)
@platform_client
def fund_payment_stream(
    platform: PlatformClient,
    network: NetworkAPI,
    account: AccountAPI,
    cluster_path: str,
    token_amount: int | None,
    stream_time: timedelta | None,
):
    """
    Fund an existing streaming payment for the given CLUSTER

    NOTE: You can fund anyone else's Stream!
    """

    if "/" not in cluster_path or len(cluster_path.split("/")) > 2:
        raise click.BadArgumentUsage(f"Invalid cluster path: '{cluster_path}'")

    workspace_name, cluster_name = cluster_path.split("/")
    if not (workspace_client := platform.workspaces.get(workspace_name)):
        raise click.BadArgumentUsage(f"Unknown workspace: '{workspace_name}'")

    elif not (cluster := workspace_client.clusters.get(cluster_name)):
        raise click.BadArgumentUsage(
            f"Unknown cluster in workspace '{workspace_name}': '{cluster_name}'"
        )

    elif cluster.status != ResourceStatus.RUNNING:
        raise click.UsageError(f"Cannot fund '{cluster_info.name}': cluster is not running.")

    elif not (stream := workspace_client.get_payment_stream(cluster, network.chain_id)):
        raise click.UsageError("Cluster is not funded via ApePay Stream")

    elif token_amount is None and stream_time is None:
        raise click.UsageError("Must specify one of '--amount' or '--time'.")

    if not token_amount:
        assert stream_time  # mypy happy
        one_token = 10 ** stream.token.decimals()
        token_amount = int(
            one_token
            * (
                stream_time.total_seconds()
                / stream.manager.compute_stream_life(
                    account.address, stream.token, one_token, stream.products
                ).total_seconds()
            )
        )

    if not stream.token.balanceOf(account) >= token_amount:
        raise click.UsageError("Do not have sufficient funding")

    elif not stream.token.allowance(account, stream.manager.address) >= token_amount:
        click.echo(f"Approving StreamManager({stream.manager.address})")
        stream.token.approve(
            stream.manager.address,
            2**256 - 1 if click.confirm("Unlimited Approval?") else token_amount,
            sender=account,
        )

    click.echo(
        f"Funding Stream for Cluster '{cluster_path}' with "
        f"{token_amount / 10**stream.token.decimals():0.4f} {stream.token.symbol()}"
    )
    stream.add_funds(token_amount, sender=account)

    click.echo(f"{click.style('SUCCESS', fg='green')}: Cluster funded for {stream.time_left}.")


@pay.command(name="cancel", cls=ConnectedProviderCommand)
@account_option()
@click.argument("cluster_path", metavar="CLUSTER")
@platform_client
def cancel_payment_stream(
    platform: PlatformClient,
    network: NetworkAPI,
    account: AccountAPI,
    cluster_path: str,
):
    """
    Shutdown CLUSTER and refund all funds to Stream owner

    NOTE: Only the Stream owner can perform this action!
    """

    if "/" not in cluster_path or len(cluster_path.split("/")) > 2:
        raise click.BadArgumentUsage(f"Invalid cluster path: '{cluster_path}'")

    workspace_name, cluster_name = cluster_path.split("/")
    if not (workspace_client := platform.workspaces.get(workspace_name)):
        raise click.BadArgumentUsage(f"Unknown workspace: '{workspace_name}'")

    elif not (cluster := workspace_client.clusters.get(cluster_name)):
        raise click.BadArgumentUsage(
            f"Unknown cluster in workspace '{workspace_name}': '{cluster_name}'"
        )

    elif cluster.status != ResourceStatus.RUNNING:
        raise click.UsageError(f"Cannot fund '{cluster_info.name}': cluster is not running.")

    elif not (stream := workspace_client.get_payment_stream(cluster, network.chain_id)):
        raise click.UsageError("Cluster is not funded via ApePay Stream")

    if click.confirm(
        click.style("This action is irreversible, are you sure?", bold=True, bg="red")
    ):
        stream.cancel(sender=account)

    click.echo(f"{click.style('WARNING', fg='yellow')}: Cluster cannot be used anymore.")


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
