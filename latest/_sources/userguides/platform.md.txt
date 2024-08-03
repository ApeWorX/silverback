# Deploying Applications

In this guide, we are going to show you more details on how to deploy your application to the [Silverback Platform](https://silverback.apeworx.io).

## Creating a Cluster

The Silverback Platform runs your Applications (or "Bots") on dedicated managed application Clusters.
These Clusters will take care to orchestrate infrastructure, monitor, run your triggers, and collect metrics for your applications.
Each Cluster is bespoke for an individual or organization, and isolates your applications from others on different infrastructure.

Before we deploy our Application, we have to create a Cluster.
If you haven't yet, please sign up for Silverback at [https://silverback.apeworx.io](https://silverback.apeworx.io).

Once you have signed up, you can actually create (and pay for) your Clusters from the Silverback CLI utility by first
logging in to the Platform using [`silverback login`][silverback-login],
and then using [`silverback cluster new`][silverback-cluster-new] to follow the steps necessary to deploy it.

```{note}
The Platform UI will let you create and manage Clusters using a graphical experience, which may be preferred.
The CLI experience is for those working locally who don't want to visit the website, or are locally developing their applications.
```

## Connecting to your Cluster

To connect to a cluster, you can use commands from the [`silverback cluster`][silverback-cluster] subcommand group.
For instance, to list all your available bots on your cluster, use [`silverback cluster bots list`][silverback-cluster-bots-list].
To obtain general information about your cluster, just use [`silverback cluster info`][silverback-cluster-info],
or [`silverback cluster health`][silverback-cluster-health] to see the current status of your Cluster.

If you have no bots, we will first have to containerize our Applications and upload them to a container registry that our Cluster is configured to access.

```{note}
Building a container for your application can be an advanced topic, we have included the `silverback build` subcommand to help assist in generating Dockerfiles.
```

## Building your Bot

TODO: Add build process and describe `silverback build --autogen` and `silverback build --upgrade`

TODO: Add how to debug containers using `silverback run` w/ `taskiq-redis` broker

## Adding Environment Variables

Once you have created your bot application container image, you might know of some environment variables the image requires to run properly.
Thanks to it's flexible plugin system, ape plugins may also require specific environment variables to load as well.
Silverback Clusters include an environment variable management system for exactly this purpose,
which you can manage using [`silverback cluster vars`][silverback-cluster-vars] subcommand.

The environment variable management system makes use of a concept called "Variable Groups" which are distinct collections of environment variables meant to be used together.
These variable groups will help in managing the runtime environment of your Applications by allowing you to segregate different variables depending on each bot's needs.

To create an environment group, use the [`silverback cluster vars new`][silverback-cluster-vars-new] command and give it a name and a set of related variables.
For instance, it may make sense to make a group of variables for your favorite Ape plugins or services, such as RPC Providers, Blockchain Data Indexers, Etherscan, etc.
You might have a database connection that you want all your bots to access.

```{warning}
All environment variables in Silverback Clusters are private, meaning they cannot be viewed after they are uploaded.
However, your Bots will have full access to their values from within their runtime environment, so be careful that you fully understand what you are sharing with your bots.

Also, understand your build dependencies within your container and make sure you are not using any vulnerable or malicious packages.

**NEVER** upload your private key in a plaintext format!

Use _Ape Account Plugins_ such as [`ape-aws`](https://github.com/ApeWorX/ape-aws) to safely manage access to your hosted keys.
```

```{note}
The Etherscan plugin _will not function_ without an API key in the cloud environment.
This will likely create errors running your applications if you use Ape's `Contract` class.
```

To list your Variable Groups, use [`silverback cluster vars list`][silverback-cluster-vars-list].
To see information about a specific Variable Group, including the Environment Variables it includes, use [`silverback cluster vars info`][silverback-cluster-vars-info]
To remove a variable group, use [`silverback cluster vars remove`][silverback-cluster-vars-remove],

```{note}
You can only remove a Variable Group if it is not referenced by any existing Bot.
```

Once you have created all the Variable Group(s) that you need to operate your Bot, you can reference these groups by name when adding your Bot to the cluster.

## Deploying your Bot

You are finally ready to deploy your bot on the Cluster and get it running!

To deploy your Bot, use the [`silverback cluster bots new`][silverback-cluster-bots-new] command and give your bot a name,
container image, network to run on, an account alias (if you want to sign transactions w/ `app.signer`),
and any environment Variable Group(s) the bot needs.
If everything validates successfully, the Cluster will begin orchestrating your deployment for you.

You should monitor the deployment and startup of your bot to make sure it enters the RUNNING state successfully.
You can do this using the [`silverback cluster bots health`][silverback-cluster-bots-health] command.

```{note}
It usually takes a minute or so for your bot to transition from PROVISIONING to STARTUP to the RUNNING state.
If there are any difficulties in downloading your container image, provisioning your desired infrastructure, or if your application encounters an error during the STARTUP phase,
the Bot will not enter into the RUNNING state and will be shut down gracefully into the STOPPED state.

Once in the STOPPED state, you can make any adjustments to the environment Variable Group(s) or other runtime parameters in the Bot config;
or, you can make code changes and deploy a new image for the Bot to use.
Once ready, you can use the `silverback cluster bots start` command to re-start your Bot.
```

If at any time you want to view the configuration of your bot, you can do so using the [`silverback cluster bots info`][silverback-cluster-bots-info] command.
You can also update metadata or configuration of your bot using the [`silverback cluster bots update`][silverback-cluster-bots-update] command.
Lastly, if you want to shutdown and delete your bot, you can do so using the [`silverback cluster bots remove`][silverback-cluster-bots-remove] command.

```{note}
Configuration updates do not redeploy your Bots automatically, you must manually stop and restart your bots for changes to take effect.
```

```{warning}
Removing a Bot will immediately trigger a SHUTDOWN if the Bot is not already STOPPED.
```

## Monitoring your Bot

Once your bot is successfully running in the RUNNING state, you can monitor your bot with a series of commands
under the [`silverback cluster bots`][silverback-cluster-bots] subcommand group.
We already saw how you can use the [`silverback cluster bots list`][silverback-cluster-bots-list] command to see all bots managed by your Cluster (running or not).

To see runtime health information about a specific bot, again use the [`silverback cluster bots health`][silverback-cluster-bots-health] command.
You can view the logs that a specific bot is generating using the [`silverback cluster bots logs`][silverback-cluster-bots-logs] command.
Lastly, you can view unacknowledged errors that your bot has experienced while in the RUNNING state
using the [`silverback cluster bots errors`][silverback-cluster-bots-errors] command.

```{warning}
Once in the RUNNING state, your Bot will not stop running unless it experiences a certain amount of errors in quick succession.
Any task execution that experiences an error will abort execution (and therefore not produce any metrics) but the Bot **will not** shutdown.

All errors encountered during task exeuction are reported to the Cluster for later review by any users with appriopiate access.
Tasks do not retry (by default), but updates to `app.state` are maintained up until the point an error occurs.

It is important to keep track of these errors and ensure that none of them are in fact critical to the operation of your Application,
and to take corrective or preventative action if it is determined that it should be treated as a more critical failure condition.
```

```{note}
Your Bots can also be monitored from the Platform UI at [https://silverback.apeworx.io](https://silverback.apeworx.io).
```

## Controlling your Bot

As we already saw, once a Bot is configured in a Cluster, we can control it using commands from the [`silverback cluster bots`][silverback-cluster-bots] subcommand group.
For example, we can attempt to start a Bot that is not currently running (after making configuration or code changes)
using the [`silverback cluster bots start`][silverback-cluster-bots-start] command.
We can also stop a bot using [`silverback cluster bots stop`][silverback-cluster-bots-stop] that is currently in the RUNNING state if we desire.

```{note}
Controlling your bots can be done from the Platform UI at [https://silverback.apeworx.io](https://silverback.apeworx.io), if you have the right permissions to do so.
```

TODO: Updating runtime parameters

## Viewing Measured Metrics

TODO: Downloading metrics from your Bot

[silverback-cluster]: ../commands/cluster.html#silverback-cluster
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
[silverback-cluster-health]: ../commands/cluster.html#silverback-cluster-health
[silverback-cluster-info]: ../commands/cluster.html#silverback-cluster-info
[silverback-cluster-new]: ../commands/cluster.html#silverback-cluster-new
[silverback-cluster-vars]: ../commands/cluster.html#silverback-cluster-vars
[silverback-cluster-vars-info]: ../commands/cluster.html#silverback-cluster-vars-info
[silverback-cluster-vars-list]: ../commands/cluster.html#silverback-cluster-vars-list
[silverback-cluster-vars-new]: ../commands/cluster.html#silverback-cluster-vars-new
[silverback-cluster-vars-remove]: ../commands/cluster.html#silverback-cluster-vars-remove
[silverback-login]: ../commands/cluster.html#silverback-login
