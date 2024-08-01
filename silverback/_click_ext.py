import click
from fief_client import Fief
from fief_client.integrations.cli import FiefAuth, FiefAuthNotAuthenticatedError

from silverback._importer import import_from_string
from silverback.cluster.client import ClusterClient, PlatformClient
from silverback.cluster.settings import (
    DEFAULT_PROFILE,
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


class AuthCommand(click.Command):
    # NOTE: ClassVar for any command to access
    profile: ClusterProfile | PlatformProfile
    auth: FiefAuth | None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.params.append(
            click.Option(
                param_decls=("-p", "--profile", "profile"),
                expose_value=False,
                metavar="PROFILE",
                default=DEFAULT_PROFILE,
                callback=self.get_profile,
                help="The profile to use to connect with the cluster (Advanced)",
            )
        )

    def get_profile(self, ctx, param, value) -> BaseProfile:

        if not (profile := settings.profile.get(value)):
            raise click.BadOptionUsage(option_name=param, message=f"Unknown profile '{value}'.")

        self.profile = profile
        self.auth = self.get_auth(profile)
        return profile

    def get_auth(self, profile: BaseProfile) -> FiefAuth | None:
        if not isinstance(profile, PlatformProfile):
            return None

        auth_info = settings.auth[profile.auth]
        fief = Fief(auth_info.host, auth_info.client_id)
        return FiefAuth(fief, str(PROFILE_PATH.parent / f"{profile.auth}.json"))

    def invoke(self, ctx: click.Context):
        callback_params = self.callback.__annotations__ if self.callback else {}

        # HACK: Click commands will fail otherwise if something is in context
        #       the callback doesn't expect, so delete these:
        if "profile" not in callback_params and "profile" in ctx.params:
            del ctx.params["profile"]

        if "auth" not in callback_params and "auth" in ctx.params:
            del ctx.params["auth"]

        return super().invoke(ctx)


class ClientCommand(AuthCommand):
    workspace_name: str | None = None
    cluster_name: str | None = None

    def __init__(self, *args, disable_cluster_option: bool = False, **kwargs):
        super().__init__(*args, **kwargs)

        if not disable_cluster_option:
            self.params.append(
                click.Option(
                    param_decls=(
                        "-c",
                        "--cluster",
                    ),
                    metavar="WORKSPACE/NAME",
                    expose_value=False,
                    callback=self.get_cluster_path,
                    help="[Platform Only] NAME of the cluster in the WORKSPACE you wish to access",
                )
            )

    def get_cluster_path(self, ctx, param, value) -> str | None:
        if isinstance(self.profile, PlatformProfile):
            if not value:
                return value

            elif "/" not in value or len(parts := value.split("/")) > 2:
                raise click.BadParameter("CLUSTER should be in format `WORKSPACE/CLUSTER-NAME`")

            self.workspace_name, self.cluster_name = parts

        elif self.profile and value:
            raise click.BadParameter("CLUSTER not needed unless using a platform profile")

        return value

    def get_platform_client(self, auth: FiefAuth, profile: PlatformProfile) -> PlatformClient:
        try:
            display_login_message(auth, profile.host)
        except FiefAuthNotAuthenticatedError as e:
            raise click.UsageError("Not authenticated, please use `silverback login` first.") from e

        return PlatformClient(
            base_url=profile.host,
            cookies=dict(session=auth.access_token_info()["access_token"]),
        )

    def invoke(self, ctx: click.Context):
        callback_params = self.callback.__annotations__ if self.callback else {}

        if "client" in callback_params:
            client_type_needed = callback_params.get("client")

            if isinstance(self.profile, PlatformProfile):
                if not self.auth:
                    raise click.UsageError(
                        "This feature is not available outside of the Silverback Platform"
                    )

                platform_client = self.get_platform_client(self.auth, self.profile)

                if client_type_needed == PlatformClient:
                    ctx.params["client"] = platform_client

                elif not self.workspace_name or not self.cluster_name:
                    raise click.UsageError(
                        "-c WORKSPACE/NAME should be present when using a Platform profile"
                    )

                else:
                    try:
                        ctx.params["client"] = platform_client.get_cluster_client(
                            self.workspace_name, self.cluster_name
                        )
                    except ValueError as e:
                        raise click.UsageError(str(e))

            elif not client_type_needed == ClusterClient:
                raise click.UsageError("A cluster profile can only directly connect to a cluster.")

            else:
                click.echo(
                    f"{click.style('INFO', fg='blue')}: Logged in to "
                    f"'{click.style(self.profile.host, bold=True)}' using API Key"
                )
                ctx.params["client"] = ClusterClient(
                    base_url=self.profile.host,
                    headers={"X-API-Key": self.profile.api_key},
                )

        return super().invoke(ctx)


class PlatformGroup(SectionedHelpGroup):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.command_class = ClientCommand
        self.group_class = PlatformGroup
