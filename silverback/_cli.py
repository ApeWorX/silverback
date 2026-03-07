import asyncio
import os
import secrets
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import click
import yaml  # type: ignore[import-untyped]
from ape import Contract, convert
from ape.cli import (
    AccountAliasPromptChoice,
    ConnectedProviderCommand,
    LazyChoice,
    account_option,
    ape_cli_context,
    network_option,
)
from ape.exceptions import Abort, ApeException, ConversionError
from ape.logging import LogLevel
from ape.types import AddressType
from apepay import Stream, StreamManager

from silverback.recorder import JSONLineRecorder

from ._click_ext import (
    SectionedHelpGroup,
    auth_required,
    bot_path_callback,
    cls_import_callback,
    cluster_client,
    env_file_callback,
    parse_globbed_arg,
    platform_client,
    timedelta_callback,
    token_amount_callback,
)
from .exceptions import ClientError
from .types import ScalarType, is_scalar_type
from .utils import get_chain_info

if TYPE_CHECKING:
    from ape.api import AccountAPI, EcosystemAPI, NetworkAPI, ProviderAPI
    from ape.contracts import ContractInstance

    from .cluster.auth import Auth
    from .cluster.client import Bot, ClusterClient, PlatformClient

LOCAL_DATETIME = "%Y-%m-%d %H:%M:%S %Z"


@click.group(cls=SectionedHelpGroup)
@click.version_option(message="%(version)s", package_name="silverback")
def cli():
    """
    Silverback: Build Python bots that react to on-chain events

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
    "--record",
    is_flag=True,
    default=False,
    help="Record this session into a session file (under `.silverback-sessions/`)",
)
@click.option(
    "--recorder",
    "recorder_class",
    metavar="CLASS_REF",
    help="An import string in format '<module>:<CustomRecorder>'",
    callback=cls_import_callback,
)
@click.option("-x", "--max-exceptions", type=int, default=3)
@click.option("--debug", is_flag=True, default=False)
@click.option(
    "--env-file",
    multiple=True,
    type=click.Path(exists=True, dir_okay=False, readable=True, resolve_path=True, path_type=Path),
    callback=env_file_callback,
    is_eager=True,
    expose_value=False,
    help=("Path to .env file(s) (multiple allowed; values override). "),
)
@click.argument("bot", required=False, callback=bot_path_callback)
def run(cli_ctx, account, runner_class, record, recorder_class, max_exceptions, debug, bot):
    """Run Silverback bot"""
    from silverback.runner import PollingRunner, WebsocketRunner

    if not runner_class:
        # NOTE: Automatically select runner class
        if cli_ctx.provider.ws_uri:
            runner_class = WebsocketRunner
        elif cli_ctx.provider.http_uri:
            runner_class = PollingRunner
        else:
            raise click.BadOptionUsage(
                option_name="network",
                message="Network choice cannot support running bot",
            )

    if record and not recorder_class:
        recorder_class = JSONLineRecorder

    runner = runner_class(
        bot,
        recorder=recorder_class() if record else None,
        max_exceptions=max_exceptions,
    )
    asyncio.run(runner.run(), debug=debug)


def convert_param(val) -> ScalarType:
    if is_scalar_type(val):
        return val

    elif val.lower() in ("f", "false"):
        return False

    elif val.lower() in ("t", "true"):
        return True

    try:
        return int(val)

    except Exception:
        pass

    try:
        return float(val)

    except Exception:
        pass

    # NOTE: Decimal allows the most values, so leave last
    return Decimal(val)


def convert_param_kwargs(ctx, param, values) -> dict[str, ScalarType]:
    converted_params = {}
    for kwarg in values:
        name, value = kwarg.split("=")
        converted_params[name] = convert_param(value)

    return converted_params


@cli.command(cls=ConnectedProviderCommand, help="Set parameters against a running silverback app")
@network_option(
    default=os.environ.get("SILVERBACK_NETWORK_CHOICE", "auto"),
    callback=_network_callback,
)
@click.option(
    "-p",
    "--param",
    "param_updates",
    multiple=True,
    callback=convert_param_kwargs,
)
@click.argument("bot", required=False, callback=bot_path_callback)
def set_param(param_updates, bot):
    if len(param_updates) == 0:
        raise click.UsageError("Must change at least one param via `-p/--param`")

    elif len(param_updates) > 1:
        kicker = bot._batch_set_param
        args = [param_updates]

    else:
        kicker = bot._set_param
        args = list(param_updates.items())[0]

    async def set_parameters():
        await bot.broker.startup()
        task = await kicker.kiq(*args)
        result = await task.wait_result()
        await bot.broker.shutdown()

        if result.is_err:
            raise click.UsageError(str(result.error))

        else:
            click.echo(result.return_value)

    asyncio.run(set_parameters())


@cli.command(section="Local Commands")
@click.option(
    "--use-docker",
    is_flag=True,
    default=False,
    help="Override podman detection and use docker to build instead.",
)
@click.option(
    "-g",
    "--generate",
    is_flag=True,
    default=False,
    help="Generate Dockerfiles first. Defaults to false.",
)
@click.option(
    "-t",
    "--tag-base",
    default=None,
    help=(
        "The base to use to tag the final bot image(s). "
        "If multiple bots are in the project, the name of the bot is appended to it, following a '-' separator. "
        "If only one bot, then `-bot` is used. "
        "Defaults to using the name of the folder you are building from."
    ),
)
@click.option(
    "--version",
    default="latest",
    metavar="VERSION",
    help="Version label to use for tagging final bot images. Defaults to 'latest'.",
)
@click.option(
    "--sdk",
    "sdk_version",
    default="stable",
    metavar="VERSION",
    help="Version of Silverback SDK to use as base image. Defaults to 'stable'.",
)
@click.option(
    "--push",
    is_flag=True,
    default=False,
    help="Push image to logged-in registry. Defaults to false.",
)
@click.argument("path", required=False, default=None)
def build(use_docker, generate, tag_base, version, sdk_version, push, path):
    """
    Generate Dockerfiles and build bot container images

    When '--tag-base' is used, you can control the base of the tag for the image.
    For example, '--tag-base project' with bots 'botA.py' and 'botB.py' (under 'bots/') produces
    '-t project-bota:latest' and '-t project-botb:latest' respectively.

    For building to push to a specific image registry, use '--tag-base' to correctly tag images.
    Using '--tag-base ghcr.io/myorg/myproject' with the previous example
    '-t ghcr.io/myorg/project-bota:latest' and '-t ghcr.io/myorg/project-botb:latest' respectively.
    """
    from silverback._build_utils import (
        IMAGES_FOLDER_NAME,
        build_container_images,
        generate_containerfiles,
    )

    if generate:
        if (
            path is not None
            and not (path := Path.cwd() / f"{path}.py").exists()
            and not (path := Path.cwd() / path).exists()
        ) or (
            path is None
            and not (path := Path.cwd() / "bots").exists()
            and not (path := Path.cwd() / "bot").exists()
            and not (path := Path.cwd() / "bot.py").exists()
        ):
            raise click.ClickException(
                f"The path '{path.relative_to(Path.cwd())}' does not exist in project. "
                "This command can auto-detect 'bot.py' or 'bot/' folder in project root"
                ", or process all '*.py' bots in  'bots/' folder."
            )

        generate_containerfiles(path, sdk_version=sdk_version)

    if not (Path.cwd() / IMAGES_FOLDER_NAME).exists():
        raise click.ClickException(
            f"The container image cache folder '{IMAGES_FOLDER_NAME}' does not exist. "
            "You can run `silverback build --generate` to generate it and build."
        )

    build_container_images(use_docker=use_docker, tag_base=tag_base, version=version, push=push)


@cli.command(cls=ConnectedProviderCommand, section="Local Commands")
@ape_cli_context()
@network_option(
    default=os.environ.get("SILVERBACK_NETWORK_CHOICE", "auto"),
    callback=_network_callback,
)
@click.option("--account", type=AccountAliasPromptChoice(), callback=_account_callback)
@click.option("-w", "--workers", type=int, default=2)
@click.option("-s", "--shutdown_timeout", type=int, default=90)
@click.option("--debug", is_flag=True, default=False)
@click.option(
    "--env-file",
    multiple=True,
    type=click.Path(exists=True, dir_okay=False, readable=True, resolve_path=True, path_type=Path),
    callback=env_file_callback,
    is_eager=True,
    expose_value=False,
    help=("Path to .env file(s) (multiple allowed; values override). "),
)
@click.argument("bot", required=False, callback=bot_path_callback)
def worker(cli_ctx, account, workers, shutdown_timeout, debug, bot):
    """Run Silverback task workers (advanced)"""
    from silverback.worker import run_worker

    asyncio.run(
        run_worker(bot.broker, worker_count=workers, shutdown_timeout=shutdown_timeout),
        debug=debug,
    )


@cli.command(section="Cloud Commands (https://silverback.apeworx.io)")
@auth_required
def login(auth: "Auth"):
    """Login to ApeWorX Authorization Service (https://account.apeworx.io)"""

    state = secrets.token_urlsafe()
    # NOTE: Should verify state, but doesn't (need it for auth though)
    auth.authorize(scope=["profile"], extras_params=dict(state=state))
    userinfo = auth.current_user()
    # TODO: Refactor once migration is completed
    username = (
        # Ory (new)
        userinfo.get("preferred_username")
        # Fief (current)
        or userinfo.get("fields", {}).get("username")
        # Fallback (for both)
        or userinfo["sub"]
    )
    click.echo(
        f"{click.style('INFO', fg='blue')}: Logged in to "
        f"'{click.style(auth.client.base_url, bold=True)}' as '{click.style(username, bold=True)}'"
    )


@cli.group(cls=SectionedHelpGroup, section="Cloud Commands (https://silverback.apeworx.io)")
def cluster():
    """Manage a Silverback hosted bot cluster

    For clusters on the Silverback Platform, please provide a name for the cluster to access under
    your platform account via `-c WORKSPACE/NAME`"""


@cluster.group(cls=SectionedHelpGroup, section="Platform Commands (https://silverback.apeworx.io)")
def workspaces():
    """View and Manage Workspaces on the Silverback Platform"""


@workspaces.command(name="list", section="Platform Commands (https://silverback.apeworx.io)")
@platform_client()
def list_workspaces(platform: "PlatformClient"):
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


@workspaces.command(name="info", section="Platform Commands (https://silverback.apeworx.io)")
@click.argument("workspace")
@platform_client()
def workspace_info(platform: "PlatformClient", workspace: str):
    """Get Configuration information about a WORKSPACE"""

    if not (workspace_info := platform.workspaces.get(workspace)):
        raise click.BadOptionUsage("workspace", f"Unknown workspace '{workspace}'")

    click.echo(f"{click.style('Name', fg='green')}: {workspace_info.name}")
    click.echo(f"{click.style('Slug', fg='green')}: '{workspace_info.slug}'")
    click.echo(
        f"{click.style('Date Created', fg='green')}: "
        f"{workspace_info.created.astimezone().strftime(LOCAL_DATETIME)}"
    )


@workspaces.command(name="new", section="Platform Commands (https://silverback.apeworx.io)")
@click.option(
    "-n",
    "--name",
    "workspace_name",
    help="Name for new workspace",
)
@click.option(
    "-s",
    "--slug",
    "workspace_slug",
    help="Slug for new workspace",
)
@platform_client()
def new_workspace(
    platform: "PlatformClient",
    workspace_name: str | None,
    workspace_slug: str | None,
):
    """Create a new workspace"""

    workspace_name = workspace_name or workspace_slug
    workspace_slug = workspace_slug or (
        workspace_name.lower().replace(" ", "-") if workspace_name else None
    )

    if not workspace_name:
        raise click.UsageError("Must provide a name or a slug/name combo")

    workspace = platform.create_workspace(
        workspace_name=workspace_name,
        workspace_slug=workspace_slug,
    )
    click.echo(
        f"{click.style('SUCCESS', fg='green')}: "
        f"Created '{workspace.name}' (slug: '{workspace.slug}')"
    )


@workspaces.command(name="update", section="Platform Commands (https://silverback.apeworx.io)")
@click.option(
    "-n",
    "--name",
    "name",
    default=None,
    help="Update name for workspace",
)
@click.option(
    "-s",
    "--slug",
    "slug",
    default=None,
    help="Update slug for workspace",
)
@click.argument("workspace")
@platform_client()
def update_workspace(
    platform: "PlatformClient",
    workspace: str,
    name: str | None,
    slug: str | None,
):
    """Update name and slug for a workspace"""

    if not (workspace_client := platform.workspaces.get(workspace)):
        raise click.BadOptionUsage("workspace", f"Unknown workspace '{workspace}'")

    elif name is None and slug is None:
        raise click.UsageError(
            "No update name or slug found. Please enter a name or slug to update."
        )
    elif name == "" or slug == "":
        raise click.UsageError("Empty string value found for name or slug.")

    updated_workspace = workspace_client.update(
        name=name,
        slug=slug,
    )
    click.echo(f"{click.style('SUCCESS', fg='green')}: Updated '{updated_workspace.name}'")


@workspaces.command(name="delete", section="Platform Commands (https://silverback.apeworx.io)")
@click.argument("workspace")
@platform_client()
def delete_workspace(platform: "PlatformClient", workspace: str):
    """Delete an empty Workspace on the Silverback Platform"""

    if not (workspace_client := platform.workspaces.get(workspace)):
        raise click.BadOptionUsage("workspace", f"Unknown workspace '{workspace}'")

    if len(workspace_client.clusters) > 0:
        raise click.UsageError("Running Clusters found in Workspace. Shut them down first.")

    workspace_client.remove()
    click.echo(f"{click.style('SUCCESS', fg='green')}: Deleted '{workspace_client.name}'")


@cluster.command(name="list", section="Platform Commands (https://silverback.apeworx.io)")
@click.argument("workspace")
@platform_client()
def list_clusters(platform: "PlatformClient", workspace: str):
    """List available clusters in a WORKSPACE"""

    if not (workspace_client := platform.workspaces.get(workspace)):
        raise click.BadOptionUsage("workspace", f"Unknown workspace '{workspace}'")

    if clusters := workspace_client.clusters.values():
        cluster_info = [f"- {cluster.name} ({cluster.status})" for cluster in clusters]
        click.echo("\n".join(cluster_info))

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
@platform_client()
def new_cluster(
    platform: "PlatformClient",
    workspace: str,
    cluster_name: str | None,
    cluster_slug: str | None,
):
    """Create a new cluster in WORKSPACE"""

    if not (workspace_client := platform.workspaces.get(workspace)):
        raise click.BadOptionUsage("workspace", f"Unknown workspace '{workspace}'")

    cluster_name = cluster_name or cluster_slug
    cluster_slug = cluster_slug or (
        cluster_name.lower().replace(" ", "-") if cluster_name else None
    )

    if not cluster_name:
        raise click.UsageError("Must provide a name or a slug/name combo")

    from silverback.cluster.types import ResourceStatus

    cluster = workspace_client.create_cluster(
        cluster_name=cluster_name,
        cluster_slug=cluster_slug,
    )
    click.echo(
        f"{click.style('SUCCESS', fg='green')}: Created '{cluster.name}' (slug: '{cluster.slug}')"
    )

    if cluster.status == ResourceStatus.CREATED:
        click.echo(
            f"{click.style('WARNING', fg='yellow')}: To use this cluster, "
            f"please pay via `silverback cluster pay create {workspace}/{cluster_slug}`"
        )


@cluster.command(name="update", section="Platform Commands (https://silverback.apeworx.io)")
@click.option(
    "-n",
    "--name",
    "name",
    default=None,
    help="Update name for cluster",
)
@click.option(
    "-s",
    "--slug",
    "slug",
    default=None,
    help="Update slug for cluster",
)
@click.argument("cluster_path")
@platform_client()
def update_cluster(
    platform: "PlatformClient",
    cluster_path: str,
    name: str | None,
    slug: str | None,
):
    """Update name and slug for a CLUSTER"""

    if "/" not in cluster_path or len(cluster_path.split("/")) > 2:
        raise click.BadArgumentUsage(f"Invalid cluster path: '{cluster_path}'")

    workspace_name, cluster_name = cluster_path.split("/")
    if not (workspace_client := platform.workspaces.get(workspace_name)):
        raise click.BadArgumentUsage(f"Unknown workspace: '{workspace_name}'")

    elif not (cluster := workspace_client.clusters.get(cluster_name)):
        raise click.BadArgumentUsage(
            f"Unknown cluster in workspace '{workspace_name}': '{cluster_name}'"
        )

    elif name is None and slug is None:
        raise click.UsageError(
            "No update name or slug found. Please enter a name or slug to update."
        )
    elif name == "" or slug == "":
        raise click.UsageError("Empty string value found for name or slug.")

    updated_cluster = workspace_client.update_cluster(
        cluster_id=str(cluster.id),
        name=name,
        slug=slug,
    )
    click.echo(f"{click.style('SUCCESS', fg='green')}: Updated '{updated_cluster.name}'")


@cluster.command(name="migrate", section="Platform Commands (https://silverback.apeworx.io)")
@click.option("--version", default=None)
@click.argument("cluster_path")
@platform_client()
def migrate_cluster(
    platform: "PlatformClient",
    cluster_path: str,
    version: str | None,
):
    """Migrate CLUSTER running software version to VERSION"""
    if "/" not in cluster_path or len(cluster_path.split("/")) > 2:
        raise click.BadArgumentUsage(f"Invalid cluster path: '{cluster_path}'")

    workspace_name, cluster_name = cluster_path.split("/")
    if not (workspace_client := platform.workspaces.get(workspace_name)):
        raise click.BadArgumentUsage(f"Unknown workspace: '{workspace_name}'")

    elif not (cluster := workspace_client.clusters.get(cluster_name)):
        raise click.BadArgumentUsage(
            f"Unknown cluster in workspace '{workspace_name}': '{cluster_name}'"
        )

    elif version and version not in (available_versions := workspace_client.available_versions):
        available_versions_str = "', '".join(available_versions)
        raise click.BadOptionUsage(
            "version",
            f"Cannot migrate to version '{version}', must be one of: '{available_versions_str}'",
        )

    click.echo(
        f"{click.style('INFO', fg='blue')}: "
        f"Migrating '{cluster_path}' from '{cluster.version}' to '{version or 'stable'}'"
    )
    workspace_client.migrate_cluster(str(cluster.id), version=version)
    click.echo(f"{click.style('SUCCESS', fg='green')}: Migration of '{cluster.name}' started")
    click.echo(
        f"{click.style('INFO', fg='blue')}: "
        "This may take a couple of minutes, check `silverback cluster info` for version change"
    )


@cluster.group(cls=SectionedHelpGroup, section="Platform Commands (https://silverback.apeworx.io)")
def pay():
    """Pay for CLUSTER with Crypto using ApePay streaming payments"""


def _default_tier():
    from silverback.cluster.types import ClusterTier

    return ClusterTier.STANDARD.name.capitalize()


def _tier_choices():
    from silverback.cluster.types import ClusterTier

    return [
        ClusterTier.STANDARD.name.capitalize(),
        ClusterTier.PREMIUM.name.capitalize(),
    ]


@pay.command(name="create", cls=ConnectedProviderCommand)
@account_option()
@click.argument("cluster_path")
@click.option(
    "-t",
    "--tier",
    default=_default_tier,
    metavar="NAME",
    type=LazyChoice(_tier_choices, case_sensitive=False),
    help="Named set of options to use for cluster as a base (Defaults to Standard)",
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
@platform_client()
def create_payment_stream(
    platform: "PlatformClient",
    network: "NetworkAPI",
    account: "AccountAPI",
    cluster_path: str,
    tier: str,
    config_updates: list[tuple[str, str]],
    token: Optional["ContractInstance"],
    token_amount: int | None,
    stream_time: timedelta | None,
):
    """
    Create a new streaming payment for a given CLUSTER

    NOTE: This action cannot be cancelled! Streams must exist for at least 1 hour before cancelling.
    """
    from silverback.cluster.types import ClusterTier, ResourceStatus

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

    accepted_tokens = platform.get_accepted_tokens(network.chain_id)
    if token:
        try:
            convert(token, AddressType)
            token_symbol = Contract(token).symbol()
        except ConversionError:
            token_symbol = token
        finally:
            token = accepted_tokens.get(token_symbol)

        if token is None:
            raise click.UsageError(f"Token not found in accepted tokens: {accepted_tokens}.")

    else:
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
    click.echo(f"duration: {stream_time}")
    click.echo(f"payment: {token_amount / (10 ** token.decimals())} {token.symbol()}\n")

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
        " Check back in 2-5 minutes using `silverback cluster info` to start using your cluster."
    )


@pay.command(name="info")
@click.argument("cluster_path", metavar="CLUSTER")
@platform_client()
def get_payment_info(platform: "PlatformClient", cluster_path: str):
    """Display streaming payment information for the given CLUSTER"""
    from silverback.cluster.types import ResourceStatus

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
        raise click.UsageError(f"Cannot fund '{cluster.name}': cluster is not running.")

    elif stream_info := workspace_client.get_stream_info(cluster):
        click.echo(f"Cluster is funded via '{stream_info}'.")

    else:
        click.echo("Cluster is not funded via ApePay Stream")


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
@platform_client()
def fund_payment_stream(
    platform: "PlatformClient",
    network: "NetworkAPI",
    account: "AccountAPI",
    cluster_path: str,
    token_amount: int | None,
    stream_time: timedelta | None,
):
    """
    Fund an existing streaming payment for the given CLUSTER

    NOTE: You can fund anyone else's Stream!
    """
    from silverback.cluster.types import ResourceStatus

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
        raise click.UsageError(f"Cannot fund '{cluster.name}': cluster is not running.")

    elif not (stream_info := workspace_client.get_stream_info(cluster)):
        raise click.UsageError("Cluster is not funded via ApePay Stream")

    elif stream_info.chain_id != network.chain_id:
        ecosystem_name, network_name = get_chain_info(stream_info.chain_id)
        raise click.UsageError(
            f"Incorrect chain to fund payment stream, should be {ecosystem_name}:{network_name}."
        )

    elif token_amount is None and stream_time is None:
        raise click.UsageError("Must specify one of '--amount' or '--time'.")

    else:
        stream = Stream(manager=StreamManager(stream_info.manager), id=stream_info.stream_id)

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
        f"{token_amount / 10 ** stream.token.decimals():0.4f} {stream.token.symbol()}"
    )
    stream.add_funds(token_amount, sender=account)

    click.echo(f"{click.style('SUCCESS', fg='green')}: Cluster funded for {stream.time_left}.")


@pay.command(name="cancel", cls=ConnectedProviderCommand)
@account_option()
@click.argument("cluster_path", metavar="CLUSTER")
@platform_client()
def cancel_payment_stream(
    platform: "PlatformClient",
    network: "NetworkAPI",
    account: "AccountAPI",
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

    elif not (stream_info := workspace_client.get_stream_info(cluster)):
        raise click.UsageError("Cluster is not funded via ApePay Stream")

    elif stream_info.chain_id != network.chain_id:
        ecosystem_name, network_name = get_chain_info(stream_info.chain_id)
        raise click.UsageError(
            f"Incorrect chain to cancel payment stream, should be {ecosystem_name}:{network_name}."
        )

    else:
        stream = Stream(manager=StreamManager(stream_info.manager), id=stream_info.stream_id)

    if click.confirm(
        click.style("This action is irreversible, are you sure?", bold=True, bg="red")
    ):
        stream.cancel(sender=account)

    click.echo(f"{click.style('WARNING', fg='yellow')}: Cluster cannot be used anymore.")


@cluster.command(name="info")
@cluster_client()
def cluster_info(cluster: "ClusterClient"):
    """Get configuration information about a CLUSTER"""

    # NOTE: This actually doesn't query the cluster's routes, which are protected
    click.echo(f"Cluster Version: v{cluster.version}")


@cluster.command(name="health")
@cluster_client()
def cluster_health(cluster: "ClusterClient"):
    """Get health information about a CLUSTER"""

    click.echo(yaml.safe_dump(cluster.health.model_dump()))


@cluster.group(cls=SectionedHelpGroup)
def registry():
    """Manage container registry credentials in CLUSTER"""


@registry.command(name="list")
@cluster_client()
def credentials_list(cluster: "ClusterClient"):
    """List container registry credentials in CLUSTER"""

    if creds := list(cluster.credentials):
        click.echo(yaml.safe_dump(creds))

    else:
        click.secho("No registry credentials present in this cluster", bold=True, fg="red")


@registry.command(name="info")
@click.argument("name")
@cluster_client()
def credentials_info(cluster: "ClusterClient", name: str):
    """Show info about credential NAME in CLUSTER's registry"""

    if not (creds := cluster.credentials.get(name)):
        raise click.UsageError(f"Unknown credentials '{name}'")

    click.echo(yaml.safe_dump(creds.model_dump(exclude={"id", "name"})))


@registry.command(name="new")
@click.argument("name")
@click.argument("registry")
@cluster_client()
def credentials_new(cluster: "ClusterClient", name: str, registry: str):
    """Add registry access credential NAME to CLUSTER's registry.

    NOTE: This command will prompt you for an EMAIL, USERNAME, and PASSWORD.
    """

    email = click.prompt("Email")
    username = click.prompt("Username")
    password = click.prompt("Password", hide_input=True)

    creds = cluster.new_credentials(
        name=name, email=email, hostname=registry, username=username, password=password
    )
    click.echo(yaml.safe_dump(creds.model_dump(exclude={"id"})))


@registry.command(name="update")
@click.argument("name")
@click.option("-r", "--registry")
@cluster_client()
def credentials_update(cluster: "ClusterClient", name: str, registry: str | None = None):
    """Update credential NAME in CLUSTER's registry

    NOTE: This command will prompt you for an EMAIL, USERNAME, and PASSWORD.
    """
    if not (creds := cluster.credentials.get(name)):
        raise click.UsageError(f"Unknown credentials '{name}'")

    email = click.prompt("Email")
    username = click.prompt("Username")
    password = click.prompt("Password", hide_input=True)

    creds = creds.update(hostname=registry, email=email, username=username, password=password)
    click.echo(yaml.safe_dump(creds.model_dump(exclude={"id"})))


@registry.command(name="remove")
@click.argument("name")
@cluster_client()
def credentials_remove(cluster: "ClusterClient", name: str):
    """Remove credential NAME from CLUSTER's registry"""
    if not (creds := cluster.credentials.get(name)):
        raise click.UsageError(f"Unknown credentials '{name}'")

    creds.remove()  # NOTE: No confirmation because can only delete if no references exist
    click.secho(f"registry credentials '{creds.name}' removed.", fg="green", bold=True)


@cluster.group(cls=SectionedHelpGroup)
def vars():
    """Manage groups of environment variables in a CLUSTER"""


def parse_envvars(ctx, name, value: list[str]) -> dict[str, str]:
    def parse_envar(item: str):
        if "=" not in item:
            if not (envvar := os.environ.get(item)):
                raise click.UsageError(
                    f"Environment variable '{item}' has no value in your environment"
                )

            return item, envvar

        elif len(item.split("=")) != 2:
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
    metavar="NAME[=VAL]",
    callback=parse_envvars,
    help="Environment variable name or key and value to add (Multiple allowed)",
)
@click.argument("name")
@cluster_client()
def new_vargroup(cluster: "ClusterClient", variables: dict, name: str):
    """Create a new group of environment variables in a CLUSTER"""

    if len(variables) == 0:
        raise click.UsageError("Must supply at least one var via `-e`")

    vg = cluster.new_variable_group(name=name, variables=variables)
    click.echo(yaml.safe_dump(vg.model_dump(exclude={"id"})))  # NOTE: Skip machine `.id`


@vars.command(name="list")
@cluster_client()
def list_vargroups(cluster: "ClusterClient"):
    """List latest revisions of all variable groups in a CLUSTER"""

    if group_names := list(cluster.variable_groups):
        click.echo(yaml.safe_dump(group_names))

    else:
        click.secho("No Variable Groups present in this cluster", bold=True, fg="red")


@vars.command(name="info")
@click.argument("name")
@cluster_client()
def vargroup_info(cluster: "ClusterClient", name: str):
    """Show information about a variable GROUP in a CLUSTER"""

    if not (vg := cluster.variable_groups.get(name)):
        raise click.UsageError(f"Unknown Variable Group '{name}'")

    click.echo(yaml.safe_dump(vg.model_dump(exclude={"id", "name"})))


@vars.command(name="update")
@click.option(
    "-e",
    "--env",
    "updated_vars",
    multiple=True,
    type=str,
    metavar="NAME[=VAL]",
    callback=parse_envvars,
    help="Environment variable name or key and value to add/update (Multiple allowed)",
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
@cluster_client()
def update_vargroup(
    cluster: "ClusterClient",
    name: str,
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
                **updated_vars,
                **{v: None for v in deleted_vars},
            ).model_dump(exclude={"id"})  # NOTE: Skip machine `.id`
        )
    )


@vars.command(name="remove")
@click.argument("name")
@cluster_client()
def remove_vargroup(cluster: "ClusterClient", name: str):
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
@network_option(required=True)
@click.option("-a", "--account")
@click.option("-g", "--group", "environment", multiple=True)
@click.option(
    "--credential",
    "credential_name",
    help="registry credentials to use to pull the image",
)
@click.option(
    "--cluster-access/--no-cluster-access",
    is_flag=True,
    default=False,
    help="Give bot access to CLUSTER (Advanced)",
)
@click.argument("name")
@cluster_client()
def new_bot(
    cluster: "ClusterClient",
    image: str,
    ecosystem: "EcosystemAPI",
    network: "NetworkAPI",
    provider: "ProviderAPI",
    account: str | None,
    environment: list[str],
    credential_name: str | None,
    cluster_access: bool,
    name: str,
):
    """Create a new bot in a CLUSTER with the given configuration"""

    if name in cluster.bots:
        raise click.UsageError(f"Cannot use name '{name}' to create bot")

    elif cluster.configuration and len(cluster.bots) >= cluster.configuration.bots:
        click.secho("Number of bots in cluster is at paid limit", bold=True, fg="yellow")

    # NOTE: Check if credentials exist
    if credential_name is not None and credential_name not in cluster.credentials:
        raise click.UsageError(f"Unknown registry credentials '{credential_name}'")

    click.echo(f"Name: '{name}'")
    click.echo(f"Image: '{image}'")
    click.echo(f"Network: '{ecosystem.name}:{network.name}:{provider.name}'")
    if environment:
        variable_groups = cluster.variable_groups
        click.echo(
            yaml.safe_dump(
                {
                    "Environment": {
                        vg.name: vg.variables
                        for vg in map(variable_groups.__getitem__, environment)
                    }
                }
            )
        )

    if credential_name is not None:
        click.echo(f"Registry credentials: {credential_name}")

    if cluster_access and not click.confirm(
        click.style(
            f"Are you sure you want to give this bot admin access to cluster '{cluster.base_url}'?",
            fg="red",
        )
    ):
        return

    if not click.confirm("Do you want to create and start running this bot?"):
        return

    bot = cluster.new_bot(
        name=name,
        image=image,
        ecosystem=ecosystem.name,
        network=network.name,
        provider=provider.name,
        account=account,
        environment=environment,
        credential_name=credential_name,
        cluster_access=cluster_access,
    )
    click.secho(f"Bot '{bot.name}' ({bot.id}) deploying...", fg="green", bold=True)


@bots.command(name="list", section="Configuration Commands")
@cluster_client()
def list_bots(cluster: "ClusterClient"):
    """List all bots in a CLUSTER by network (Regardless of status)"""

    if bots := list(cluster.bots.values()):
        groups: dict[str, dict[str, list["Bot"]]] = defaultdict(lambda: defaultdict(list))
        for bot in bots:
            groups[bot.ecosystem][bot.network].append(bot)

        for ecosystem, networks in groups.items():
            click.echo(f"{ecosystem}:")
            for network, bots_by_network in networks.items():
                click.echo(f"    {network}:")
                for bot in sorted(bots_by_network, key=lambda b: b.name):
                    click.echo(f"""      - {bot.name}""")

    else:
        click.secho("No bots in this cluster", bold=True, fg="red")


@bots.command(name="info", section="Configuration Commands")
@click.argument("name", metavar="BOT", default="*")
@cluster_client()
def bot_info(cluster: "ClusterClient", name: str):
    """Get configuration information of one or more BOT(s) in a CLUSTER"""

    for bot in parse_globbed_arg(name, cluster.bots):
        bot_dump = bot.model_dump(
            exclude={
                "id",  # not needed
                "name",  # key
                # TODO: Remove from model
                "credential_name",
                # Must render later on
                "environment",
                # Display condensed version instead
                "ecosystem",
                "network",
                "provider",
            }
        )
        bot_dump["network"] = f"{bot.ecosystem}:{bot.network}:{bot.provider}"
        if bot.environment:
            bot_dump["environment"] = [
                var.model_dump(exclude={"id", "created"}) for var in bot.vargroups
            ]

        click.echo(yaml.safe_dump({bot.name: bot_dump}))


@bots.command(name="update", section="Configuration Commands")
@click.option("--new-name", "new_name")  # NOTE: No shorthand, because conflicts w/ `--network`
@click.option("-i", "--image")
@click.option("-n", "--network", default=None)
@click.option("-a", "--account", default="<no-change>")
@click.option("-g", "--group", "environment", multiple=True)
@click.option("--clear-vars", "clear_environment", is_flag=True)
@click.option(
    "--credential",
    "credential_name",
    default="<no-change>",
    help="registry credentials to use to pull the image",
)
@click.option(
    "--cluster-access/--no-cluster-access",
    is_flag=True,
    default=None,
    help="Give bot access to CLUSTER. Defaults to no change (Advanced)",
)
@click.argument("name", metavar="BOT")
@cluster_client()
def update_bot(
    cluster: "ClusterClient",
    new_name: str | None,
    image: str | None,
    network: str | None,
    account: str | None,
    environment: list[str],
    clear_environment: bool,
    credential_name: str | None,
    cluster_access: bool | None,
    name: str,
):
    """Update configuration of BOT in CLUSTER

    NOTE: Some configuration updates will trigger a redeploy"""

    if new_name in cluster.bots:
        raise click.UsageError(f"Cannot use name '{new_name}' to update bot '{name}'")

    if not (bot := cluster.bots.get(name)):
        raise click.UsageError(f"Unknown bot '{name}'.")

    if new_name is not None:
        click.echo(f"Name:\n  old: {name}\n  new: {new_name}")

    ecosystem, provider = None, None
    if network is not None:
        network_choice = network.split(":")
        ecosystem = network_choice[0] or None
        network = network_choice[1] or None if len(network_choice) >= 2 else None
        provider = network_choice[2] or None if len(network_choice) == 3 else None

    if (
        (ecosystem is not None and bot.ecosystem != ecosystem)
        or (network is not None and bot.network != network)
        or (provider is not None and bot.provider != provider)
    ):
        click.echo("Network:")
        click.echo(f"  old: '{bot.ecosystem}:{bot.network}:{bot.provider}'")
        new_network_choice = (
            f"{ecosystem or bot.ecosystem}:{network or bot.network}:{provider or bot.provider}"
        )
        click.echo(f"  new: '{new_network_choice}'")

    if (
        credential_name is not None
        and credential_name != "<no-change>"
        and credential_name not in cluster.credentials
    ):  # NOTE: Check if credentials exist
        raise click.UsageError(f"Unknown credential '{credential_name}'")

    redeploy_required = False
    if image:
        redeploy_required = True
        click.echo(f"Image:\n  old: {bot.image}\n  new: {image}")

    if clear_environment or (environment and bot.environment != list(environment)):
        variable_groups = cluster.variable_groups
        env: dict[str, dict[str, list[str]]] = dict(old={}, new={})

        for vg_name in bot.environment:
            if vg := variable_groups.get(vg_name):
                env["old"][vg_name] = vg.variables

            else:
                click.secho(f"Variable Group missing: '{vg_name}'", bold=True, fg="red")

        if not clear_environment:
            for vg_name in environment:
                if vg := variable_groups.get(vg_name):
                    env["new"][vg_name] = vg.variables

                else:
                    raise click.BadOptionUsage(
                        "environment", f"Variable Group doesn't exist: '{vg_name}'"
                    )

        click.echo(yaml.safe_dump({"Environment": env}))

    redeploy_required |= clear_environment

    if cluster_access and not click.confirm(
        click.style(
            f"Are you sure you want to give this bot admin access to cluster '{cluster.base_url}'?",
            fg="red",
        )
    ):
        return
    redeploy_required |= cluster_access is not None

    if not click.confirm(
        f"Do you want to update '{name}'?"
        if not redeploy_required
        else f"Do you want to update and redeploy '{name}'?"
    ):
        return

    bot = bot.update(
        name=new_name,
        image=image,
        ecosystem=ecosystem,
        network=network,
        provider=provider,
        account=account,
        environment=environment if environment or clear_environment else None,
        credential_name=credential_name,
        cluster_access=cluster_access,
    )

    # NOTE: Skip machine `.id`
    bot_dump = bot.model_dump(
        exclude={
            "id",
            "name",
            "credential_name",
            "environment",
            "ecosystem",
            "network",
            "provider",
        }
    )
    bot_dump["network"] = f"{bot.ecosystem}:{bot.network}:{bot.provider}"

    if bot.credential:
        bot_dump["credential"] = bot.credential.model_dump(exclude={"id", "name"})

    click.echo(yaml.safe_dump(bot_dump))
    if bot.environment:
        click.echo("environment:")
        click.echo(
            yaml.safe_dump([var.model_dump(exclude={"id", "created"}) for var in bot.vargroups])
        )


@bots.command(name="remove", section="Configuration Commands")
@click.argument("name", metavar="BOT", default="*")
@cluster_client()
def remove_bot(cluster: "ClusterClient", name: str):
    """Remove one or more BOT(s) from CLUSTER (Shutdown if running)"""

    for bot in parse_globbed_arg(name, cluster.bots):
        if not click.confirm(f"Do you want to shutdown and delete '{bot.name}'?"):
            bot.remove()
            click.secho(f"Bot '{bot.name}' removed.", fg="green", bold=True)


@bots.command(name="health", section="Bot Operation Commands")
@click.argument("name", metavar="BOT", default="*")
@cluster_client()
def bot_health(cluster: "ClusterClient", name: str):
    """Show current health of one or more BOT(s) in a CLUSTER"""

    for bot in parse_globbed_arg(name, cluster.bots):
        if bot.is_healthy:
            bot_status = click.style("healthy", fg="green")
        else:
            bot_status = click.style("not healthy", fg="red")

        click.echo(f"{bot.name}: {bot_status}")


@bots.command(name="start", section="Bot Operation Commands")
@click.argument("name", metavar="BOT", default="*")
@cluster_client()
def start_bot(cluster: "ClusterClient", name: str):
    """Start one or more BOT(s) running in CLUSTER (if stopped or terminated)"""

    for bot in parse_globbed_arg(name, cluster.bots):
        if click.confirm(f"Do you want to start running '{bot.name}'?"):
            try:
                bot.start()
                click.secho(f"Bot '{bot.name}' starting...", fg="green", bold=True)
            except ClientError as e:
                click.secho(f"Error starting '{bot.name}': {e}", fg="red")


@bots.command(name="stop", section="Bot Operation Commands")
@click.argument("name", metavar="BOT", default="*")
@cluster_client()
def stop_bot(cluster: "ClusterClient", name: str):
    """Stop one or more BOT(s) from running in CLUSTER (if running)"""

    for bot in parse_globbed_arg(name, cluster.bots):
        if not click.confirm(f"Do you want to stop '{bot.name}' from running?"):
            try:
                bot.stop()
                click.secho(f"Bot '{bot.name}' stopping...", fg="green", bold=True)
            except ClientError as e:
                click.secho(f"Error stopping '{bot.name}': {e}", fg="red")


@bots.command(name="logs", section="Bot Operation Commands")
@click.argument("name", metavar="BOT")
@click.option(
    "-l",
    "--log-level",
    "log_level",
    help="Minimum log level to display.",
    default="INFO",
)
@click.option(
    "-s",
    "--since",
    "since",
    help="Return logs since N ago.",
    callback=timedelta_callback,
)
@click.option(
    "-f",
    "--follow",
    help="Stream logs as they come in",
    is_flag=True,
    default=False,
)
@cluster_client()
def show_bot_logs(
    cluster: "ClusterClient",
    name: str,
    log_level: str,
    since: timedelta | None,
    follow: bool,
):
    """Show runtime logs for BOT in CLUSTER"""

    start_time = None
    if since:
        start_time = datetime.now(tz=timezone.utc) - since

    if not (bot := cluster.bots.get(name)):
        raise click.UsageError(f"Unknown bot '{name}'.")

    try:
        level = LogLevel.__dict__[log_level.upper()]
    except KeyError:
        level = LogLevel.INFO

    for log in bot.get_logs(log_level=level, start_time=start_time, follow=follow):
        click.echo(str(log))


@bots.command(name="errors", section="Bot Operation Commands")
@click.argument("name", metavar="BOT", default="*")
@cluster_client()
def show_bot_errors(cluster: "ClusterClient", name: str):
    """Show unacknowledged errors for one or more BOT(s) in CLUSTER"""

    for bot in parse_globbed_arg(name, cluster.bots):
        click.echo(f"'{bot.name}' errors:")
        for log in bot.errors:
            click.echo(log)


@cluster.command(name="mcp", section="Platform Commands (https://silverback.apeworx.io)")
@cluster_client(show_login=False)
def run_mcp_server(cluster: "ClusterClient"):
    """Run MCP (Model Context Protocol) Server"""

    try:
        from .cluster import mcp
    except ImportError:
        raise click.UsageError(
            "You must install the `mcp` package (or use `silverback[mcp]` extra)"
        )

    # NOTE: Need to inject this into context so it has access
    mcp.client = cluster
    mcp.server.run()
