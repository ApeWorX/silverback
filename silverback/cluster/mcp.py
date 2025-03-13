from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fief_client import Fief
from fief_client.integrations.cli import FiefAuth
from mcp.server.fastmcp import Context, FastMCP  # type: ignore[import-not-found]

from silverback.cluster.client import PlatformClient
from silverback.cluster.settings import PROFILE_PATH, PlatformProfile, ProfileSettings
from silverback.cluster.types import ClusterHealth


@dataclass
class AppContext:
    platform: PlatformClient


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    settings = ProfileSettings.from_config_file()

    # TODO: Allow loading arbitrary profiles
    profile = settings.profile["staging"]
    assert isinstance(profile, PlatformProfile)

    auth = FiefAuth(
        Fief(
            settings.auth[profile.auth].host,
            settings.auth[profile.auth].client_id,
        ),
        str(PROFILE_PATH.parent / f"{profile.auth}.json"),
    )
    platform = PlatformClient(
        base_url=profile.host,
        cookies=dict(session=auth.access_token_info()["access_token"]),
    )
    yield AppContext(platform=platform)


mcp = FastMCP("silverback", dependencies=["silverback"], lifespan=lifespan)


@mcp.resource("workspace://")
def list_workspaces(ctx: Context) -> list[str]:
    """Get the list of all available Workspaces"""
    platform = ctx.request_context.lifespan_context["platform"]
    return list(platform.workspaces)


@mcp.resource("workspace://{workspace_name}")
def list_clusters(workspace_name: str, ctx: Context) -> list[str]:
    """Get the list of all Cluster names in a specific Workspace."""
    platform = ctx.request_context.lifespan_context["platform"]
    return list(platform.workspaces[workspace_name].clusters)


@mcp.resource("cluster://{workspace_name}/{cluster_name}/health")
def cluster_health(workspace_name: str, cluster_name: str, ctx: Context) -> ClusterHealth:
    """Obtain the health of Bots and Networks in connected Cluster."""
    platform = ctx.request_context.lifespan_context["platform"]
    cluster = platform.get_cluster_client(workspace_name, cluster_name)
    return cluster.health
