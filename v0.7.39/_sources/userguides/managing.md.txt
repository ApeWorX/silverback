# Silverback Platform

In this guide, we are going to show you more details on how to deploy and host your bots
in the cloud with the [Silverback Platform](https://silverback.apeworx.io).

```{note}
The Platform UI will let you create and manage Workspaces and Clusters using a graphical experience, which may be preferred.
The CLI experience is for those working locally who don't want to visit the website, or for use locally when developing bots.
It can also be used directly by an LLM, but we recommend using the [Model Context Server][#model-context-server] instead.
```

## Requirements

If you haven't already signed up, please visit [https://silverback.apeworx.io](https://silverback.apeworx.io) and sign up first.
Then log in to the Platform using [`silverback login`][silverback-login] from the CLI command.

## Managing a Workspace

A Workspace is an area for one or more users to co-manage a set of clusters together.
You can manage workspaces from the Silverback CLI using [`silverback cluster workspaces`][silverback-cluster-workspaces].

Using the Silverback CLI you can [`list` workspaces][silverback-cluster-workspaces-list], [make `new` ones][silverback-cluster-workspaces-new], [view their configuration `info`rmation][silverback-cluster-workspaces-info], [`update` that information][silverback-cluster-workspaces-update], as well as [`delete` them][silverback-cluster-workspaces-delete].

```{note}
You need at least one workspace to deploy a cluster.
It is recommended that you create a personal workspace first.
```

## Creating a Cluster

The Silverback Platform runs your Bots on dedicated Clusters.
These Clusters will take care to orchestrate infrastructure, monitor, run triggers,
and collect metrics for all of your bots you have added to them.
Each Cluster is run on bespoke infrastructure, which isolates your bots from those of other users
by virtue of running in a segregated deployment.

Once you have a workspace, you can create new Clusters from the Silverback CLI using
the [`silverback cluster new`][silverback-cluster-new] command.
Just follow the steps it gives you to finish deploying it, and (if needed) pay for it.

You can also use the Silverback CLI to [`list`][silverback-cluster-list]
and [`update`][silverback-cluster-update] existing clusters.

### Paying for a Cluster

Once you have created your Cluster, you will likely have to fund it so it is made available for your use.
To do that, use the [`silverback cluster pay create`][silverback-cluster-pay-create] command to fund your newly created cluster.
Please note that provisioning your cluster will take time, and it may take up to an hour for it to be ready.
Check back after 5-15 minutes using the [`silverback cluster info`][silverback-cluster-info] command to see when it's ready for use.

At any point after the Cluster is funded, you can fund it for more time via [`silverback cluster pay add-time`][silverback-cluster-pay-add-time]
command to extend the timeline that the Cluster is hosted for.
Note that it is possible for anyone to add more time to the Cluster, at any time and for any amount.

```{important}
If that hosting time expires, the Platform will automatically de-provision your infrastructure.
**It is not possible to reverse this!**
The Platform may send you notifications when your Stream is close to expiring,
but it is up to you to remember to fill it so it doesn't expire.
Note that your collected data will stay available for up to 30 days allowing you the ability
to download any data you need after it's been removed.
```

Lastly, if you ever feel like you want to delete your Cluster, you can cancel the funding for it
and instantly get a refund for the remaining time!
If you are the owner of the Stream, you can do this via the
[`silverback cluster pay cancel`][silverback-cluster-pay-cancel] command.

```{important}
Only the owner may do this, so if you are not the owner you should contact them to have them do that action for you.
```

## Adding Environment Variables

Before adding bots, you might know of some environment variables they require to run properly.
Thanks to it's flexible plugin system, ape plugins may also require specific environment variables to function as well.
Silverback Clusters include an environment variable management system for exactly this purpose,
which you can manage using [`silverback cluster vars`][silverback-cluster-vars] subcommand.

The environment variable management system makes use of a concept called "Variable Groups",
which are distinct collections of environment variables meant to be used together.
These variable groups will help in managing the runtime environment of your Bots by allowing
you to segregate different variables depending on each bot's needs.

To create an environment group, use the [`silverback cluster vars new`][silverback-cluster-vars-new]
command and give it a name and a set of related variables.
For instance, it may make sense to make a group of variables for your favorite Ape plugins or services,
such as RPC Providers, Blockchain Data Indexers, Etherscan, etc.
You might also have a database connection that you want all your bots to access.

```{warning}
All environment variables in Silverback Clusters are private,
meaning they cannot be viewed after they are uploaded.
However, your Bots will have full access to their values from within their runtime environment,
so be careful that you fully understand what you are sharing with your bots in each variable group.

**NEVER** upload a private key in a plaintext format!

Use _Ape Account Plugins_ such as [`ape-aws`](https://github.com/ApeWorX/ape-aws) to safely manage access to your hosted keys.
```

```{note}
The Etherscan plugin _will not function_ without an API key in the cloud environment.
This will likely create errors running your bots if you use Ape's `Contract` class.
```

To list existing Variable Groups, use [`silverback cluster vars list`][silverback-cluster-vars-list].
To see information about a specific Variable Group, including the Environment Variables it includes,
use [`silverback cluster vars info`][silverback-cluster-vars-info]
To remove a variable group, use [`silverback cluster vars remove`][silverback-cluster-vars-remove].

```{important}
You can only remove a Variable Group if it is not referenced by any existing Bot.
```

Once you have created all the Variable Group(s) that you need to operate your Bot,
you can reference these groups by name when adding your Bot to the cluster.

## Private Container Registries

If you are using a private container registry to store your images,
you will need to provide your bot with the necessary credentials to access it.
First you will need to add your credentials to the cluster with the
[`silverback cluster registry new`][silverback-cluster-registry-new] command.

Once added, the Cluster will automatically use these credentials when fetching private images.

## Deploying your Bot

You are finally ready to deploy your bot on the Cluster and get it running!

To deploy your Bot, use the [`silverback cluster bots new`][silverback-cluster-bots-new] command and give your bot a name,
container image, network to run on, an account alias (if you want to sign transactions w/ `bot.signer`),
and any environment Variable Group(s) the bot needs to function (Etherscan, RPC API Key, etc.).
If everything validates successfully, the Cluster will begin orchestrating your deployment for you.

You should monitor the deployment and startup of your bot to make sure it enters the RUNNING state successfully.
You can do this using the [`silverback cluster bots health`][silverback-cluster-bots-health] command.

```{important}
It usually takes a minute or so for your bot to transition from PROVISIONING to STARTUP to the RUNNING state.
If there are any difficulties in downloading your container image, provisioning your desired infrastructure,
or if your bot encounters an error during the STARTUP phase,
the Bot will not enter into the RUNNING state and will be shut down gracefully into the STOPPED state.

Once in the STOPPED state, you can make any adjustments to the environment Variable Group(s)
or other runtime parameters in the Bot config; or, you can make code changes and deploy a new image for the Bot to use.
Once ready, you can use the `silverback cluster bots start` command to re-start your Bot.
```

If at any time you want to view the configuration of your bot, you can do so using the
[`silverback cluster bots info`][silverback-cluster-bots-info] command.
You can also update metadata or configuration of your bot using the
[`silverback cluster bots update`][silverback-cluster-bots-update] command.
Lastly, if you want to shutdown and delete your bot, you can do so using the
[`silverback cluster bots remove`][silverback-cluster-bots-remove] command.

```{important}
Configuration updates do not redeploy your Bots automatically,
you must manually stop and restart your bots for changes to take effect.
This is done so that you have full control over when this happens.
```

```{warning}
Removing a Bot will immediately trigger a SHUTDOWN if the Bot is not already STOPPED.
```

## Monitoring your Bot

Once your bot is successfully running in the RUNNING state,
you can monitor your bot with a series of commands under the
[`silverback cluster bots`][silverback-cluster-bots] subcommand group.
We already saw how you can use the [`silverback cluster bots list`][silverback-cluster-bots-list]
command to see all bots managed by your Cluster (running or not).

To see runtime health information about a specific bot, again use the
[`silverback cluster bots health`][silverback-cluster-bots-health] command.
You can view the logs that a specific bot is generating using the
[`silverback cluster bots logs`][silverback-cluster-bots-logs] command.
Lastly, you can view unacknowledged errors that your bot has experienced while in the RUNNING state
using the [`silverback cluster bots errors`][silverback-cluster-bots-errors] command.

```{warning}
Once in the RUNNING state, your Bot will not stop running unless it experiences
a certain amount of errors in quick succession (fault persistence).
Any task execution that experiences an error will abort execution
(and therefore not produce any metrics) but the Bot **will not** shutdown until the fault persistence is tripped.

All errors encountered during task exeuction are reported to the Cluster for later review by any users with appriopiate access.
Tasks do not retry (by default), but updates to `bot.state` are maintained up until the point the error occured.

It is important to keep track of these errors and ensure that none of them are in fact critical to the operation of your Bot,
and to take corrective or preventative action if it is determined that it should be treated as a more critical failure condition.
```

```{note}
Your Bots can also be monitored from the Platform UI at [https://silverback.apeworx.io](https://silverback.apeworx.io).
```

## Controlling your Bot

As we already saw, once a Bot is configured in a Cluster,
we can control it using commands from the [`silverback cluster bots`][silverback-cluster-bots] subcommand group.
For example, we can attempt to start a Bot that is not currently running (after making configuration or code changes)
using the [`silverback cluster bots start`][silverback-cluster-bots-start] command.
We can also stop a bot using [`silverback cluster bots stop`][silverback-cluster-bots-stop]
that is currently in the RUNNING state if we desire.

```{note}
Controlling your bots can be done from the Platform UI at
[https://silverback.apeworx.io](https://silverback.apeworx.io),
if you have the right permissions to do so.
```

<!-- TODO: Updating runtime parameters -->

<!-- TODO: Downloading metrics from your Bot -->

## Model Context Server

The Silverback package ships with an MCP ([Model Context Protocol](https://modelcontextprotocol.io))
which you can use via the [`silverback cluster mcp`][silverback-cluster-mcp] command.
This MCP server must be configured to run locally,
and the easiest way to do so is to configure it in your LLM of choice.
The config for using this with Claude Desktop is as follows:

`~/.config/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    // Other MCP servers go here...
    "silverback": {
      "command": "<path to `uvx` or `uv` binary>",
      "args": [
        "silverback[mcp]",
        "cluster",
        "mcp"
        // Add `--cluster <cluster name>` to use a Cluster other than your profile default
      ]
    }
  }
}
```

Once that has been configured, you can ask your LLM to do things like check the status of your cluster, summarize logs of running bots, and restart bots just talking to it!

```{note}
The MCP will use the context from [`silverback login`][silverback-login] to execute, so be sure to log in before starting.
```

[silverback-cluster-bots]: ../commands/cluster.html#silverback-cluster-bots
[silverback-cluster-bots-errors]: ../commands/cluster.html#silverback-cluster-bots-errors
[silverback-cluster-bots-health]: ../commands/cluster.html#silverback-cluster-bots-health
[silverback-cluster-bots-info]: ../commands/cluster.html#silverback-cluster-bots-info
[silverback-cluster-bots-list]: ../commands/cluster.html#silverback-cluster-bots-list
[silverback-cluster-bots-logs]: ../commands/cluster.html#silverback-cluster-bots-logs
[silverback-cluster-bots-new]: ../commands/cluster.html#silverback-cluster-bots-new
[silverback-cluster-bots-remove]: ../commands/cluster.html#silverback-cluster-bots-remove
[silverback-cluster-bots-start]: ../commands/cluster.html#silverback-cluster-bots-start
[silverback-cluster-bots-stop]: ../commands/cluster.html#silverback-cluster-bots-stop
[silverback-cluster-bots-update]: ../commands/cluster.html#silverback-cluster-bots-update
[silverback-cluster-info]: ../commands/cluster.html#silverback-cluster-info
[silverback-cluster-list]: ../commands/cluster.html#silverback-cluster-list
[silverback-cluster-mcp]: ../commands/cluster.html#silverback-cluster-mcp
[silverback-cluster-new]: ../commands/cluster.html#silverback-cluster-new
[silverback-cluster-pay-add-time]: ../commands/cluster.html#silverback-cluster-pay-add-time
[silverback-cluster-pay-cancel]: ../commands/cluster.html#silverback-cluster-pay-cancel
[silverback-cluster-pay-create]: ../commands/cluster.html#silverback-cluster-pay-create
[silverback-cluster-registry-new]: ../commands/cluster.html#silverback-cluster-registry-new
[silverback-cluster-update]: ../commands/cluster.html#silverback-cluster-update
[silverback-cluster-vars]: ../commands/cluster.html#silverback-cluster-vars
[silverback-cluster-vars-info]: ../commands/cluster.html#silverback-cluster-vars-info
[silverback-cluster-vars-list]: ../commands/cluster.html#silverback-cluster-vars-list
[silverback-cluster-vars-new]: ../commands/cluster.html#silverback-cluster-vars-new
[silverback-cluster-vars-remove]: ../commands/cluster.html#silverback-cluster-vars-remove
[silverback-cluster-workspaces]: ../commands/cluster.html#silverback-cluster-workspaces
[silverback-cluster-workspaces-delete]: ../commands/cluster.html#silverback-cluster-workspaces-delete
[silverback-cluster-workspaces-info]: ../commands/cluster.html#silverback-cluster-workspaces-info
[silverback-cluster-workspaces-list]: ../commands/cluster.html#silverback-cluster-workspaces-list
[silverback-cluster-workspaces-new]: ../commands/cluster.html#silverback-cluster-workspaces-new
[silverback-cluster-workspaces-update]: ../commands/cluster.html#silverback-cluster-workspaces-update
[silverback-login]: ../commands/cluster.html#silverback-login
