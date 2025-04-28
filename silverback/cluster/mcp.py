from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastmcp import Context, FastMCP

from silverback.cluster.client import ClusterClient
from silverback.cluster.types import (
    BotInfo,
    BotLogEntry,
    ClusterConfiguration,
    ClusterHealth,
    VariableGroupInfo,
)

# NOTE: Only work with one client at a time (to reduce # of tools)
# TODO: figure out a less janky way to do this
client: ClusterClient | None = None


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[ClusterClient]:
    assert client, "Need to inject client into `context.client`"
    yield client


server = FastMCP(
    name="Silverback Cluster",
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


@server.tool()
def cluster_url(ctx: Context) -> str:
    """Get the name of the Cluster"""
    cluster: ClusterClient = ctx.request_context.lifespan_context

    return str(cluster.base_url)


@server.tool()
def cluster_version(ctx: Context) -> str:
    """Get the software version of the Cluster"""
    cluster: ClusterClient = ctx.request_context.lifespan_context

    return cluster.version


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


# NOTE: Do not allow updating docker credentials (use CLI)
# NOTE: Do now allow MCP to transmit API keys and such (therefore no new vargroups)


@server.tool()
def list_variable_groups(ctx: Context) -> list[str]:
    """List all bots in the Cluster"""
    cluster: ClusterClient = ctx.request_context.lifespan_context

    return list(cluster.variable_groups)


@server.tool()
def variable_group_info(ctx: Context, vargroup_name: str) -> VariableGroupInfo:
    """List all bots in the Cluster"""
    cluster: ClusterClient = ctx.request_context.lifespan_context

    if not (vg := cluster.variable_groups.get(vargroup_name)):
        raise RuntimeError("Unknown Variable Group")

    return vg


# NOTE: Do *not* allow MCP to update or delete variable groups, only display them


@server.tool()
def new_bot(
    ctx: Context,
    name: str,
    image: str,
    ecosystem: str,
    network: str,
    provider: str,
    account: str | None = None,
    environment: list[str] | None = None,
) -> BotInfo:
    """Create a new bot using the given configration, and start running it"""
    cluster: ClusterClient = ctx.request_context.lifespan_context

    return cluster.new_bot(
        name=name,
        image=image,
        ecosystem=ecosystem,
        network=network,
        provider=provider,
        account=account,
        environment=environment,
    )


@server.tool()
def list_bots(ctx: Context) -> list[str]:
    """List all bots in the Cluster"""
    cluster: ClusterClient = ctx.request_context.lifespan_context

    return list(cluster.bots)


@server.tool()
def bot_info(ctx: Context, bot_name: str) -> BotInfo:
    """Get information about a particular bot in the Cluster"""
    cluster: ClusterClient = ctx.request_context.lifespan_context

    if not (bot := cluster.bots.get(bot_name)):
        raise RuntimeError("Unknown bot")

    return bot


@server.tool()
def update_bot(
    ctx: Context,
    bot_name: str,
    new_name: str | None = None,
    new_image: str | None = None,
    new_ecosystem: str | None = None,
    new_network: str | None = None,
    new_provider: str | None = None,
    new_account: str | None = "<no-change>",
    new_environment: list[str] | None = None,
) -> BotInfo:
    """Remove a particular bot from the Cluster"""
    cluster: ClusterClient = ctx.request_context.lifespan_context

    if not (bot := cluster.bots.get(bot_name)):
        raise RuntimeError("Unknown bot")

    return bot.update(
        name=new_name,
        image=new_image,
        ecosystem=new_ecosystem,
        network=new_network,
        provider=new_provider,
        account=new_account,
        environment=new_environment,
    )


@server.tool()
def remove_bot(ctx: Context, bot_name: str):
    """Remove a particular bot from the Cluster"""
    cluster: ClusterClient = ctx.request_context.lifespan_context

    if not (bot := cluster.bots.get(bot_name)):
        raise RuntimeError("Unknown bot")

    bot.remove()


@server.tool()
def bot_logs(ctx: Context, bot_name: str) -> list[BotLogEntry]:
    """Get logs from a running bot by name in the Cluster"""
    cluster: ClusterClient = ctx.request_context.lifespan_context

    if not (bot := cluster.bots.get(bot_name)):
        raise RuntimeError("Unknown bot")

    return bot.logs


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
