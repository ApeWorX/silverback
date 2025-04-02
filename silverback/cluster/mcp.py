from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from mcp.server.fastmcp import Context, FastMCP  # type: ignore[import-not-found]

from silverback.cluster.client import ClusterClient, PlatformClient
from silverback.cluster.types import BotInfo, ClusterHealth


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


@server.resource("workspace://")
def list_workspaces(ctx: Context) -> list[str]:
    """Get the list of all available Workspaces"""
    if not isinstance(
        platform := ctx.request_context.lifespan_context.client,
        PlatformClient,
    ):
        raise RuntimeError("Platform-only command")

    return list(platform.workspaces)


@server.resource("workspace://{workspace_name}")
def list_clusters(workspace_name: str, ctx: Context) -> list[str]:
    """Get the list of all Cluster names in a specific Workspace."""
    if not isinstance(
        platform := ctx.request_context.lifespan_context.client,
        PlatformClient,
    ):
        raise RuntimeError("Platform-only command")
    return list(platform.workspaces[workspace_name].clusters)


@server.resource("bots://{workspace_name}/{cluster_name}/{bot_name}")
def bot_info(workspace_name: str, cluster_name: str, bot_name: str, ctx: Context) -> BotInfo:
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
    if not isinstance(
        client := ctx.request_context.lifespan_context.client,
        ClusterClient,
    ):
        client = client.get_cluster_client(workspace_name, cluster_name)

    if not (bot := client.bots.get(bot_name)):
        raise RuntimeError("Unknown bot")

    bot.stop()
