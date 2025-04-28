# Silverback Platform

In this guide, we are going to show you more details on how to manage get set up locally with the [Silverback Platform](https://silverback.apeworx.io) to get your bots to production.

You can signup for Silverback to get full access for free at [https://silverback.apeworx.io](https://silverback.apeworx.io).

```{note}
The Platform UI will let you create and manage Workspaces and Clusters using a graphical experience, which may be preferred.
The CLI experience is for those working locally who don't want to visit the website, or are locally developing their bots.
```

Once you have signed up, you can actually create (and pay for) your Clusters from the Silverback CLI utility by first
logging in to the Platform using [`silverback login`][silverback-login].

## Managing a Workspace

A Workspace is an area for one or more people to co-manage a set of clusters together. You can manage workspaces from the Silverback CLI using [`silverback cluster workspaces`][silverback-cluster-workspaces].

Using the Silverback CLI you can [list workspaces][silverback-cluster-workspaces-list], [make new ones][silverback-cluster-workspaces-new], [view their configuration information][silverback-cluster-workspaces-info], [update their metadata][silverback-cluster-workspaces-update], as well as [delete them][silverback-cluster-workspaces-delete].

## Managing a Cluster

The Silverback Platform runs your Bots on dedicated managed Clusters.
These Clusters will take care to orchestrate infrastructure, monitor, run triggers, and collect metrics for all of your bots you have added to them.
Each Cluster is bespoke for an individual or organization, and isolates your bots from other users by virtual of running on different infrastructure.

Once you have a workspace, you can create (and pay for) your Clusters from the Silverback CLI using [`silverback cluster new`][silverback-cluster-new] to follow the steps necessary to deploy it. You can also use the Silverback CLI to [list][silverback-cluster-list] and [update][silverback-cluster-update] existing clusters.

### Deploying a Cluster

Once you have created your Cluster, you have to fund it so it is made available for your use.
To do that, use the [`silverback cluster pay create`][silverback-cluster-pay-create] command to fund your newly created cluster.
Please note that provisioning your cluster will take time, and it may take up to an hour for it to be ready.
Check back after 10-15 minutes using the [`silverback cluster info`][silverback-cluster-info] command to see when it's ready.

At any point after the Cluster is funded, you can fund it with more funds via [`silverback cluster pay add-time`][silverback-cluster-pay-add-time]
command to extend the timeline that the Cluster is kept around for.
Note that it is possible for anyone to add more time to the Cluster, at any time and for any amount.

If that timeline expires, the Platform will automatically de-provision your infrastructure, and it is not possible to reverse this!
The Platform may send you notifications when your Stream is close to expiring, but it is up to you to remember to fill it so it doesn't.
Note that your data collection will stay available for up to 30 days allowing you the ability to download any data you need.

Lastly, if you ever feel like you want to delete your Cluster, you can cancel the funding for it and get a refund of the remaining funds.
If you are the owner of the Stream, you can do this via the [`silverback cluster pay cancel`][silverback-cluster-pay-cancel] command.
Only the owner may do this, so if you are not the owner you should contact them to have them do that action for you.

## Model Context Server

The Silverback package ships with an MCP ([Model Context Protocol](https://modelcontextprotocol.io/quickstart/user)) which you can use via the [`silverback cluster mcp`][silverback-cluster-mcp] command.
This MCP server must be configured to run locally, and the easiest way to do so is to configure it in your LLM of choice.
The config for using this with Claude Desktop is as follows:

`~/.config/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    ...  # Other MCP servers go here
    "silverback": {
      "command": "<path to `uvx` or `uv` binary>",
      "args": [
        "silverback[mcp]",
        "cluster",
        "mcp"
        # Add args `--cluster <cluster name>` to use a Cluster other than your default
      ]
    }
  }
}
```

Once that has been configured, you can ask your LLM to do things like check the status of your cluster, summarize logs of running bots, and restart bots just be commanding it through a chat interface!

```{notice}
The MCP will use the context from [`silverback login`][silverback-login] to execute, so be sure to log in before starting.
```

[silverback-cluster-info]: ../commands/cluster.html#silverback-cluster-info
[silverback-cluster-list]: ../commands/cluster.html#silverback-cluster-list
[silverback-cluster-mcp]: ../commands/cluster.html#silverback-cluster-mcp
[silverback-cluster-new]: ../commands/cluster.html#silverback-cluster-new
[silverback-cluster-pay-add-time]: ../commands/cluster.html#silverback-cluster-pay-add-time
[silverback-cluster-pay-cancel]: ../commands/cluster.html#silverback-cluster-pay-cancel
[silverback-cluster-pay-create]: ../commands/cluster.html#silverback-cluster-pay-create
[silverback-cluster-update]: ../commands/cluster.html#silverback-cluster-update
[silverback-cluster-workspaces]: ../commands/cluster.html#silverback-cluster-workspaces
[silverback-cluster-workspaces-delete]: ../commands/cluster.html#silverback-cluster-workspaces-delete
[silverback-cluster-workspaces-info]: ../commands/cluster.html#silverback-cluster-workspaces-info
[silverback-cluster-workspaces-list]: ../commands/cluster.html#silverback-cluster-workspaces-list
[silverback-cluster-workspaces-new]: ../commands/cluster.html#silverback-cluster-workspaces-new
[silverback-cluster-workspaces-update]: ../commands/cluster.html#silverback-cluster-workspaces-update
[silverback-login]: ../commands/cluster.html#silverback-login
