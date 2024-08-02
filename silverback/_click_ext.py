from functools import update_wrapper
from pathlib import Path

import click
from fief_client import Fief
from fief_client.integrations.cli import FiefAuth, FiefAuthNotAuthenticatedError

from silverback._importer import import_from_string
from silverback.cluster.client import ClusterClient, PlatformClient
from silverback.cluster.settings import (
    PROFILE_PATH,
    BaseProfile,
    ClusterProfile,
    PlatformProfile,
    ProfileSettings,
)

# NOTE: only load once
settings = ProfileSettings.from_config_file()


def cls_import_callback(ctx, param, cls_name):
    if cls_name is None:
        return None  # User explicitly provided None

    elif cls := import_from_string(cls_name):
        return cls

    # If class not found, `import_from_string` returns `None`, so raise
    raise click.BadParameter(message=f"Failed to import {param} class: '{cls_name}'.")


class OrderedCommands(click.Group):
    # NOTE: Override so we get the list ordered by definition order
    def list_commands(self, ctx: click.Context) -> list[str]:
        return list(self.commands)


class SectionedHelpGroup(OrderedCommands):
    """Section commands into help groups"""

    sections: dict[str | None, list[click.Command | click.Group]]

    def __init__(self, *args, section=None, **kwargs):
        self.section = section or "Commands"
        self.sections = kwargs.pop("sections", {})
        commands = {}

        for section, command_list in self.sections.items():
            for cmd in command_list:
                cmd.section = section
                commands[cmd.name] = cmd

        super().__init__(*args, commands=commands, **kwargs)

    def command(self, *args, **kwargs):
        section = kwargs.pop("section", "Commands")
        decorator = super().command(*args, **kwargs)

        def new_decorator(f):
            cmd = decorator(f)
            cmd.section = section
            self.sections.setdefault(section, []).append(cmd)
            return cmd

        return new_decorator

    def format_commands(self, ctx, formatter):
        for section, cmds in self.sections.items():
            rows = []
            for subcommand in self.list_commands(ctx):
                cmd = self.get_command(ctx, subcommand)

                if cmd is None or cmd.section != section:
                    continue

                rows.append((subcommand, cmd.get_short_help_str(formatter.width) or ""))

            if rows:
                with formatter.section(section):
                    formatter.write_dl(rows)


def display_login_message(auth: FiefAuth, host: str):
    userinfo = auth.current_user()
    user_id = userinfo["sub"]
    username = userinfo["fields"].get("username")
    click.echo(
        f"{click.style('INFO', fg='blue')}: "
        f"Logged in to '{click.style(host, bold=True)}' as "
        f"'{click.style(username if username else user_id, bold=True)}'"
    )


def profile_option(f):
    expose_value = "profile" in f.__annotations__

    def get_profile(ctx: click.Context, param, value) -> BaseProfile:
        if not (profile := settings.profile.get(value)):
            raise click.BadOptionUsage(option_name=param, message=f"Unknown profile '{value}'.")

        # Add it to context in case we need it elsewhere
        ctx.obj = ctx.obj or {}
        ctx.obj["profile"] = profile
        return profile

    opt = click.option(
        "-p",
        "--profile",
        "profile",
        metavar="PROFILE",
        default=settings.default_profile,
        callback=get_profile,
        expose_value=expose_value,
        is_eager=True,  # NOTE: Required to ensure that `profile` is always set, even if not provied
        help="The authentication profile to use (Advanced)",
    )
    return opt(f)


def auth_required(f):
    expose_value = "auth" in f.__annotations__

    @profile_option
    @click.pass_context
    def add_auth(ctx: click.Context, *args, **kwargs):
        ctx.obj = ctx.obj or {}
        profile: BaseProfile | None = ctx.obj.get("profile")

        if isinstance(profile, PlatformProfile):
            auth_info = settings.auth[profile.auth]
            fief = Fief(auth_info.host, auth_info.client_id)
            ctx.obj["auth"] = FiefAuth(fief, str(PROFILE_PATH.parent / f"{profile.auth}.json"))

            if expose_value:
                kwargs["auth"] = ctx.obj["auth"]

        return ctx.invoke(f, *args, **kwargs)

    return update_wrapper(add_auth, f)


def platform_client(f):
    expose_value = "platform" in f.__annotations__

    @auth_required
    @click.pass_context
    def get_platform_client(ctx: click.Context, *args, **kwargs):
        ctx.obj = ctx.obj or {}
        if not isinstance(profile := ctx.obj.get("profile"), PlatformProfile):
            if not expose_value:
                return ctx.invoke(f, *args, **kwargs)

            raise click.UsageError("This command only works with the Silverback Platform")

        # NOTE: `auth` should be set if `profile` is set and is `PlatformProfile`
        auth: FiefAuth = ctx.obj["auth"]

        try:
            display_login_message(auth, profile.host)
        except FiefAuthNotAuthenticatedError as e:
            raise click.UsageError("Not authenticated, please use `silverback login` first.") from e

        ctx.obj["platform"] = PlatformClient(
            base_url=profile.host,
            cookies=dict(session=auth.access_token_info()["access_token"]),
        )

        if expose_value:
            kwargs["platform"] = ctx.obj["platform"]

        return ctx.invoke(f, *args, **kwargs)

    return update_wrapper(get_platform_client, f)


def cluster_client(f):

    def inject_cluster(ctx, param, value: str | None):
        ctx.obj = ctx.obj or {}
        if not (profile := ctx.obj.get("profile")):
            raise AssertionError("Shouldn't happen, fix cli")

        elif isinstance(profile, ClusterProfile):
            return value  # Ignore processing this for cluster clients

        elif value is None or "/" not in value:
            if not profile.default_workspace:
                raise click.UsageError(
                    "Must provide `-c CLUSTER`, or set `profile.<profile-name>.default-workspace` "
                    f"in your `~/{PROFILE_PATH.relative_to(Path.home())}`"
                )

            if value is None and profile.default_workspace not in profile.default_cluster:
                raise click.UsageError(
                    "Must provide `-c CLUSTER`, or set "
                    "`profile.<profile-name>.default-cluster.<workspace-name>` "
                    f"in your `~/{PROFILE_PATH.relative_to(Path.home())}`"
                )

            parts = [
                profile.default_workspace,
                # NOTE: `value` works as cluster selector, if set
                value or profile.default_cluster[profile.default_workspace],
            ]

        elif len(parts := value.split("/")) > 2:
            raise click.BadParameter(
                param=param,
                message="CLUSTER should be in format `WORKSPACE/NAME`",
            )

        ctx.obj["cluster_path"] = parts
        return parts

    @click.option(
        "-c",
        "--cluster",
        "cluster_path",
        metavar="WORKSPACE/NAME",
        expose_value=False,  # We don't actually need this exposed
        callback=inject_cluster,
        help="NAME of the cluster in WORKSPACE you wish to access",
    )
    @platform_client
    @click.pass_context
    def get_cluster_client(ctx: click.Context, *args, **kwargs):
        ctx.obj = ctx.obj or {}
        if isinstance(profile := ctx.obj.get("profile"), ClusterProfile):
            kwargs["cluster"] = ClusterClient(
                base_url=profile.host,
                headers={"X-API-Key": profile.api_key},
            )

        elif isinstance(profile, PlatformProfile):
            platform: PlatformClient = ctx.obj["platform"]
            kwargs["cluster"] = platform.get_cluster_client(*ctx.obj["cluster_path"])

        else:
            raise AssertionError("Profile not set, something wrong")

        return ctx.invoke(f, *args, **kwargs)

    return update_wrapper(get_cluster_client, f)
