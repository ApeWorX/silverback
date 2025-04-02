from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from mcp.server.fastmcp import Context, FastMCP  # type: ignore[import-not-found]

from silverback.cluster.client import ClusterClient, PlatformClient
from silverback.cluster.types import BotInfo, ClusterHealth, ClusterInfo, WorkspaceInfo


@dataclass
class AppContext:
    client: PlatformClient | ClusterClient | None = None


# TODO: figure out a less janky way to do this
context = AppContext()


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    assert context.client, "Need to inject client into `context.client`"
    yield context


server = FastMCP("Silverback Platform", lifespan=lifespan)


@server.resource("silverback://workspaces")
def list_workspaces(ctx: Context) -> list[str]:
    """Get all available Workspaces in the Platform"""
    if not isinstance(
        platform := ctx.request_context.lifespan_context.client,
        PlatformClient,
    ):
        raise RuntimeError("Platform-only command")

    return list(platform.workspaces)


@server.resource("silverback://workspaces/{workspace_name}")
def workspace_info(workspace_name: str, ctx: Context) -> WorkspaceInfo:
    """Get information about a particular Workspace in the Platform"""
    if not isinstance(
        platform := ctx.request_context.lifespan_context.client,
        PlatformClient,
    ):
        raise RuntimeError("Platform-only command")

    elif not (workspace := platform.workspaces.get(workspace_name)):
        raise RuntimeError

    return workspace


@server.resource("silverback://workspaces/{workspace_name}/clusters")
def list_clusters(workspace_name: str, ctx: Context) -> list[str]:
    """Get all Clusters by name under a specific Workspace in the Platform"""
    if not isinstance(
        platform := ctx.request_context.lifespan_context.client,
        PlatformClient,
    ):
        raise RuntimeError("Platform-only command")

    elif not (workspace := platform.workspaces.get(workspace_name)):
        raise RuntimeError

    return list(workspace.clusters)


@server.resource("silverback://workspaces/{workspace_name}/clusters/{cluster_name}")
def cluster_info(workspace_name: str, cluster_name: str, ctx: Context) -> ClusterInfo:
    """Get information about a particular Cluster, under a Workspace in the Platform"""
    if not isinstance(
        platform := ctx.request_context.lifespan_context.client,
        PlatformClient,
    ):
        raise RuntimeError("Platform-only command")

    elif not (workspace := platform.workspaces.get(workspace_name)):
        raise RuntimeError

    elif not (cluster := workspace.clusters.get(cluster_name)):
        raise RuntimeError

    return cluster


@server.resource("silverback://workspaces/{workspace_name}/clusters/{cluster_name}/bots")
def list_bots(workspace_name: str, cluster_name: str, ctx: Context) -> list[str]:
    """List all bots in a particular Cluster, under a Workspace in the Platform"""
    if not isinstance(
        client := ctx.request_context.lifespan_context.client,
        ClusterClient,
    ):
        client = client.get_cluster_client(workspace_name, cluster_name)

    return list(client.bots)


@server.resource("silverback://{workspace_name}/{cluster_name}/bots/{bot_name}")
def bot_info(workspace_name: str, cluster_name: str, bot_name: str, ctx: Context) -> BotInfo:
    """Get information about a particular bot, under a particular Cluster"""
    if not isinstance(
        client := ctx.request_context.lifespan_context.client,
        ClusterClient,
    ):
        client = client.get_cluster_client(workspace_name, cluster_name)

    if not (bot := client.bots.get(bot_name)):
        raise RuntimeError("Unknown bot")

    return bot


@server.tool()
def cluster_health(workspace_name: str, cluster_name: str, ctx: Context) -> ClusterHealth:
    """Obtain the health of Bots and Networks in connected Cluster."""
    if not isinstance(
        client := ctx.request_context.lifespan_context.client,
        ClusterClient,
    ):
        client = client.get_cluster_client(workspace_name, cluster_name)

    return client.health


@server.tool()
def start_bot(workspace_name: str, cluster_name: str, bot_name: str, ctx: Context):
    """Start a bot by name in connected Cluster"""
    if not isinstance(
        client := ctx.request_context.lifespan_context.client,
        ClusterClient,
    ):
        client = client.get_cluster_client(workspace_name, cluster_name)

    if not (bot := client.bots.get(bot_name)):
        raise RuntimeError("Unknown bot")

    bot.start()


@server.tool()
def stop_bot(workspace_name: str, cluster_name: str, bot_name: str, ctx: Context):
    """Stop a bot by name in connected Cluster"""
    if not isinstance(
        client := ctx.request_context.lifespan_context.client,
        ClusterClient,
    ):
        client = client.get_cluster_client(workspace_name, cluster_name)

    if not (bot := client.bots.get(bot_name)):
        raise RuntimeError("Unknown bot")

    bot.stop()
