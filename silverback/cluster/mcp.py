from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.fastmcp import Context, FastMCP  # type: ignore[import-not-found]

from silverback.cluster.client import ClusterClient
from silverback.cluster.types import BotInfo, ClusterConfiguration, ClusterHealth, VariableGroupInfo

# TODO: figure out a less janky way to do this
client: ClusterClient | None = None


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[ClusterClient]:
    assert client, "Need to inject client into `context.client`"
    yield client


server = FastMCP(
    name="silverback-cluster",
    transport=["sse"],
    lifespan=lifespan,
    instructions="""
    # Silverback Cluster MCP Server

    This server provides tools to access Clusters managed
    by the Silverback Platform (https://silverback.apeworx.io)
    """,
)


@server.prompt()
def cluster_is_okay() -> str:
    """Create a prompt that asks if the cluster is okay"""
    return "What is the health of my Silverback cluster?"


# TODO: Refactor to using resources when the following are implemented:
#       - https://github.com/modelcontextprotocol/python-sdk/pull/248
#       - https://github.com/pydantic/pydantic-ai/issues/1273
# @server.resource("silverback://version")
@server.tool()
def cluster_version(ctx: Context) -> str:
    """Get the software version of the Cluster"""
    cluster: ClusterClient = ctx.request_context.lifespan_context

    return cluster.version


# @server.resource("silverback://configuration")
@server.tool()
def cluster_configuration(ctx: Context) -> ClusterConfiguration | None:
    """Get the software version of the Cluster"""
    cluster: ClusterClient = ctx.request_context.lifespan_context

    return cluster.configuration


@server.tool()
def cluster_health(ctx: Context) -> ClusterHealth:
    """Obtain the health of Bots and Networks in the Cluster"""
    cluster: ClusterClient = ctx.request_context.lifespan_context
    return cluster.health


# @server.resource("silverback://variable-groups")
@server.tool()
def list_variable_groups(ctx: Context) -> list[str]:
    """List all bots in the Cluster"""
    cluster: ClusterClient = ctx.request_context.lifespan_context

    return list(cluster.variable_groups)


# @server.resource("silverback://variable-groups/{vargroup_name}")
@server.tool()
def variable_group_info(ctx: Context, vargroup_name: str) -> VariableGroupInfo:
    """List all bots in the Cluster"""
    cluster: ClusterClient = ctx.request_context.lifespan_context

    if not (vg := cluster.variable_groups.get(vargroup_name)):
        raise RuntimeError("Unknown Variable Group")

    return vg


# @server.resource("silverback://bots")
@server.tool()
def list_bots(ctx: Context) -> list[str]:
    """List all bots in the Cluster"""
    cluster: ClusterClient = ctx.request_context.lifespan_context

    return list(cluster.bots)


# @server.resource("silverback://bots/{bot_name}")
@server.tool()
def bot_info(ctx: Context, bot_name: str) -> BotInfo:
    """Get information about a particular bot in the Cluster"""
    cluster: ClusterClient = ctx.request_context.lifespan_context

    if not (bot := cluster.bots.get(bot_name)):
        raise RuntimeError("Unknown bot")

    return bot


@server.tool()
def bot_logs(ctx: Context, bot_name: str) -> list[str]:
    """Get logs from a running bot by name in the Cluster"""
    cluster: ClusterClient = ctx.request_context.lifespan_context

    if not (bot := cluster.bots.get(bot_name)):
        raise RuntimeError("Unknown bot")

    return [log.message for log in bot.logs]


@server.tool()
def start_bot(ctx: Context, bot_name: str):
    """Start a bot by name in the Cluster"""
    cluster: ClusterClient = ctx.request_context.lifespan_context

    if not (bot := cluster.bots.get(bot_name)):
        raise RuntimeError("Unknown bot")

    bot.start()


@server.tool()
def stop_bot(ctx: Context, bot_name: str):
    """Stop a bot by name in the Cluster"""
    cluster: ClusterClient = ctx.request_context.lifespan_context

    if not (bot := cluster.bots.get(bot_name)):
        raise RuntimeError("Unknown bot")

    bot.stop()
