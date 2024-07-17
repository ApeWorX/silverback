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
from silverback.cluster.client import ClusterClient, PlatformClient
from silverback.cluster.settings import (
    DEFAULT_PROFILE,
    PROFILE_PATH,
    BaseProfile,
    PlatformProfile,
    ProfileSettings,
)
from silverback.cluster.types import ClusterTier, render_dict_as_yaml
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


def display_login_message(auth: FiefAuth, host: str):
    userinfo = auth.current_user()
    user_id = userinfo["sub"]
    username = userinfo["fields"].get("username")
    click.echo(
        f"{click.style('INFO', fg='blue')}: "
        f"Logged in to '{click.style(host, bold=True)}' as "
        f"'{click.style(username if username else user_id, bold=True)}'"
    )


class PlatformCommand(click.Command):
    # NOTE: ClassVar assures only loaded once
    settings = ProfileSettings.from_config_file()

    # NOTE: Cache this class-wide
    platform_client: PlatformClient | None = None

    def get_params(self, ctx: click.Context):
        params = super().get_params(ctx)

        def get_profile(ctx, param, value) -> BaseProfile:

            if not (profile := self.settings.profile.get(value)):
                raise click.BadOptionUsage(option_name=param, message=f"Unknown profile '{value}'.")

            return profile

        params.append(
            click.Option(
                param_decls=("-p", "--profile", "profile"),
                metavar="PROFILE",
                default=DEFAULT_PROFILE,
                callback=get_profile,
            )
        )

        params.append(
            click.Argument(
                param_decls=("cluster",),
                metavar="WORKSPACE/CLUSTER",
                required=False,
                default=None,
            ),
        )

        return params

    def get_auth(self, profile: BaseProfile) -> FiefAuth:
        if not isinstance(profile, PlatformProfile):
            raise click.UsageError(
                "This feature is not available outside of the Silverback Platform"
            )

        auth_info = self.settings.auth[profile.auth]
        fief = Fief(auth_info.host, auth_info.client_id)
        return FiefAuth(fief, str(PROFILE_PATH.parent / f"{profile.auth}.json"))

    def get_platform_client(self, auth: FiefAuth, profile: PlatformProfile) -> PlatformClient:
        try:
            display_login_message(auth, profile.host)
        except FiefAuthNotAuthenticatedError as e:
            raise click.UsageError("Not authenticated, please use `silverback login` first.") from e

        return PlatformClient(
            base_url=profile.host,
            cookies=dict(session=auth.access_token_info()["access_token"]),
        )

    def get_cluster_client(self, cluster_path):
        assert self.platform_client, "Something parsing out of order"

        if "/" not in cluster_path or len(cluster_path.split("/")) > 2:
            raise click.BadArgumentUsage("CLUSTER should be in format `WORKSPACE/CLUSTER-NAME`")

        workspace_name, cluster_name = cluster_path.split("/")
        try:
            return self.platform_client.get_cluster_client(workspace_name, cluster_name)
        except ValueError as e:
            raise click.UsageError(str(e))

    def invoke(self, ctx: click.Context):
        callback_params = self.callback.__annotations__ if self.callback else {}

        cluster_path = ctx.params.pop("cluster")

        if "profile" not in callback_params:
            profile = ctx.params.pop("profile")

        else:
            profile = ctx.params["profile"]

        if "auth" in callback_params:
            ctx.params["auth"] = self.get_auth(profile)

        if "client" in callback_params:
            client_type_needed = callback_params.get("client")

            if isinstance(profile, PlatformProfile):
                self.platform_client = self.get_platform_client(
                    ctx.params.get("auth", self.get_auth(profile)), profile
                )

                if client_type_needed == PlatformClient:
                    ctx.params["client"] = self.platform_client

                else:
                    ctx.params["client"] = self.get_cluster_client(cluster_path)

            elif not client_type_needed == ClusterClient:
                raise click.UsageError("A cluster profile can only directly connect to a cluster.")

            else:
                click.echo(
                    f"{click.style('INFO', fg='blue')}: Logged in to "
                    f"'{click.style(profile.host, bold=True)}' using API Key"
                )
                ctx.params["client"] = ClusterClient(
                    base_url=profile.host,
                    headers={"X-API-Key": profile.api_key},
                )

            assert ctx.params["client"], "Something went wrong"

        super().invoke(ctx)


@cli.command(cls=PlatformCommand)
def login(auth: FiefAuth):
    """
    CLI Login to Managed Authorization Service

    Initiate a login in to the configured service using the given auth PROFILE.
    Defaults to https://account.apeworx.io if PROFILE not provided.

    NOTE: You likely do not need to use an auth PROFILE here.
    """

    auth.authorize()
    display_login_message(auth, auth.client.base_url)


@cli.group(cls=OrderedCommands)
def cluster():
    """Connect to hosted application clusters"""


@cluster.command(cls=PlatformCommand)
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


@cluster.command(name="list", cls=PlatformCommand)
@click.argument("workspace")
def list_clusters(client: PlatformClient, workspace: str):
    """[Platform Only] List available clusters in WORKSPACE"""

    if not (workspace_client := client.workspaces.get(workspace)):
        raise click.BadOptionUsage("workspace", f"Unknown workspace '{workspace}'")

    if cluster_display := render_dict_as_yaml(workspace_client.clusters):
        click.echo(cluster_display)

    else:
        click.secho("No clusters for this account", bold=True, fg="red")


@cluster.command(name="new", cls=PlatformCommand)
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


# `silverback cluster pay WORKSPACE/CLUSTER_NAME --account ALIAS --time "10 days"`
# TODO: Create a signature scheme for ClusterInfo
#         (ClusterInfo configuration as plaintext, .id as nonce?)
# TODO: Test payment w/ Signature validation of extra data


@cluster.command(cls=PlatformCommand)
def status(client: ClusterClient):
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


@env.command(cls=PlatformCommand)
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
def add(client: ClusterClient, variables: dict, name: str):
    """Create a new GROUP of environment variables in CLUSTER"""
    if len(variables) == 0:
        raise click.UsageError("Must supply at least one var via `-e`")

    try:
        click.echo(render_dict_as_yaml(client.new_env(name=name, variables=variables)))

    except RuntimeError as e:
        raise click.UsageError(str(e))


@env.command(name="list", cls=PlatformCommand)
def list_envs(client: ClusterClient):
    """List latest revisions of all variable groups in CLUSTER"""
    if all_envs := render_dict_as_yaml(client.envs):
        click.echo(all_envs)

    else:
        click.secho("No envs in this cluster", bold=True, fg="red")


@env.command(cls=PlatformCommand)
@click.argument("name")
@click.argument("new_name")
def change_name(client: ClusterClient, name: str, new_name: str):
    """Change the display name of a variable GROUP in CLUSTER"""
    if not (env := client.envs.get(name)):
        raise click.UsageError(f"Unknown Variable Group '{name}'")

    click.echo(render_dict_as_yaml(env.update(name=new_name)))


@env.command(name="set", cls=PlatformCommand)
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


@env.command(cls=PlatformCommand)
@click.argument("name")
@click.option("-r", "--revision", type=int, help="Revision of GROUP to show (Defaults to latest)")
def show(client: ClusterClient, name: str, revision: int | None):
    """Show all variables in latest revision of GROUP in CLUSTER"""
    if not (env := client.envs.get(name)):
        raise click.UsageError(f"Unknown Variable Group '{name}'")

    for env_info in env.revisions:
        if revision is None or env_info.revision == revision:
            click.echo(render_dict_as_yaml(env_info))
            return

    raise click.UsageError(f"Revision {revision} of '{name}' not found")


@env.command(cls=PlatformCommand)
@click.argument("name")
def rm(client: ClusterClient, name: str):
    """
    Remove a variable GROUP from CLUSTER

    NOTE: Cannot delete if any bots reference any revision of GROUP
    """
    if not (env := client.envs.get(name)):
        raise click.UsageError(f"Unknown Variable Group '{name}'")

    env.rm()
    click.secho(f"Variable Group '{env.name}' removed.", fg="green", bold=True)


@cluster.group(cls=OrderedCommands)
def bot():
    """Commands for managing bots in a CLUSTER"""


@bot.command(name="list", cls=PlatformCommand)
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


