# Managing Your Platform

In this guide, we are going to show you more details on how to manage your [Silverback Platform](https://silverback.apeworx.io).

## Managing Workspaces 

## Creating a Cluster

The Silverback Platform runs your Bots on dedicated managed application Clusters.
These Clusters will take care to orchestrate infrastructure, monitor, run your triggers, and collect metrics for your applications.
Each Cluster is bespoke for an individual or organization, and isolates your applications from others on different infrastructure.

Before we deploy our Bot, we have to create a Cluster.
If you haven't yet, please sign up for Silverback at [https://silverback.apeworx.io](https://silverback.apeworx.io).

Once you have signed up, you can actually create (and pay for) your Clusters from the Silverback CLI utility by first
logging in to the Platform using [`silverback login`][silverback-login],
and then using [`silverback cluster new`][silverback-cluster-new] to follow the steps necessary to deploy it.

```{note}
The Platform UI will let you create and manage Clusters using a graphical experience, which may be preferred.
The CLI experience is for those working locally who don't want to visit the website, or are locally developing their applications.
```

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

Lastly, if you ever feel like you no longer need your Cluster, you can cancel the funding for it and get a refund of the remaining funds.
If you are the owner of the Stream, you can do this via the [`silverback cluster pay cancel`][silverback-cluster-pay-cancel] command.
Only the owner may do this, so if you are not the owner you should contact them to have them do that action for you.

## Connecting to your Cluster

To connect to a cluster, you can use commands from the [`silverback cluster`][silverback-cluster] subcommand group.
For instance, to list all your available bots on your cluster, use [`silverback cluster bots list`][silverback-cluster-bots-list].
To obtain general information about your cluster, just use [`silverback cluster info`][silverback-cluster-info],
or [`silverback cluster health`][silverback-cluster-health] to see the current status of your Cluster.

If you have no bots, we will first have to containerize our Bots and upload them to a container registry that our Cluster is configured to access.

```{note}
Building a container for your application can be an advanced topic, we have included the `silverback build` subcommand to help assist in generating Dockerfiles.
```
