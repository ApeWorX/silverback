# Advanced Topics

There are a number of "advanced topics" that you might want to understand before using Silverback,
especially for production use. The following topics are shared not in any particular order.

## Handling Reorgs

For some chains, there is a risk of a "reorg", or block reorganization, that may impact Silverback's
handler functions as function calls could be triggered repeatedly with the same arguments provided.
Block reorganizations are an inherent risk to your bot's design, and should be handled accordingly.

```{important}
Reorg handling in subscription methods is not a completely specified in the Ethereum RPC specification
(see [execution specs](https://github.com/ethereum/execution-apis/issues/496)),
and may be handled in different fashions based on the chain's consensus technique and client parameterization.
```

### Block Reorgs

When a chain experiences a reorg, typically what will occur is that block subscriptions will "walk
backwards" to find the last valid block emitted to subscribers from the canonical chain, and then
re-emit new blocks starting from the last block shared in common (but with different content).
In order to properly handle reorgs, you should make sure that important uses of block data are
_indexed by number_, and overwrite the recorded data at those indexes with the newer data.

As a concrete example, we have an application that tracks a parameter `last_hash` in `bot.state`.
We don't really have a problem yet because while we do get the "rolled back" blocks as a part of
the new canonical chain, we are only interested in what the value of the last block is:

```py
@bot.on_(chain.blocks)
def update_last_hash(blk):
    bot.state.last_hash = blk.hash
```

Here is a call sequence of how this function might get called in a reorg scenario:

```py
update_last_hash(blk1) # .hash: abc
update_last_hash(blk2) # .hash: def
update_last_hash(blk3) # .hash: ghi
# Reorg happens! `blk2` (.hash: def) and onwards has changed
update_last_hash(blk2) # .hash: jkl, replaced .hash: def
update_last_hash(blk3) # .hash: mno, replaced .hash: ghi
```

Our handling is safe because we just want whatever the last valid block's hash was.
_However_, we have to be careful how we use this identifier,
because that value now needs to be considered tainted for downstream use.
Let's say for example we are dumping this data into a database for the purpose of tracking blocks,
and we decide to use the block's hash as our primary key to track them:

```py
@bot.on_(chain.blocks)
def update_db(blk):
    bot.state.db["blocks"][blk.hash] = blk
```

The records in our database still won't have conflicts (as the primary key is content-addressing the whole block),
_but_ we likely have a problem now because there are **2 different blocks** having the same value for field `.number`!
This is likely to cause issues downstream when using our database and trying to fetch a sequence of blocks ordered by number.
So instead, we actually want to use the `.number` field as the primary key so that when we update the record at that key,
we are actually _replacing_ the old block with the new one (and we will always have a single, canonical history):

```py
@bot.on_(chain.blocks)
def update_db(blk):
    bot.state.db["blocks"][blk.number] = blk
```

A very simple change that now handles rollbacks occuring!

```{note}
Please see [the Geth documentation](https://geth.ethereum.org/docs/interacting-with-geth/rpc/pubsub#newheads)
for more information on how block reorgs work.
```

### Event Log Reorgs

There is a similar issue when using contract event log subscriptions,
but the issue can be resolved in a more straightforward way.
As of [Ape v0.8.43](https://github.com/ApeWorX/ape/pull/2727),
our event log model now includes the `.removed` field,
which is set to `True` if the log is no longer included in the canonical chain.
It will then "re-issue" the same log if the transaction it originated from replays on the new set of blocks.

Let's use the same database analogy again.
This time, we are storing account balances for ERC20 token transfers in our database (indexed by account).
Our naive implementation of this might look like:

```py
@bot.on_(ERC20.Transfer)
def track_balances(log):
    bot.state.db["balances"][log.receiver] += log.amount
    bot.state.db["balances"][log.sender] -= log.amount
```

However when a re-org occurs, we are actually going to get the same log a second time but with the `.removed` field set,
indicating the operation has been "reversed".
Without handling this we will "double count" the event and have the wrong answer
(the balances mismatches the on-chain `.balanceOf` call).
We can easily handle this with just a little bit of logic added to "reverse" the operation in our off-chain index:

```py
@bot.on_(ERC20.Transfer)
def track_balances(log):
    if log.removed:  # NOTE: Undo balance update
        bot.state.db["balances"][log.receiver] -= log.amount
        bot.state.db["balances"][log.sender] += log.amount
    else:
        bot.state.db["balances"][log.receiver] += log.amount
        bot.state.db["balances"][log.sender] -= log.amount
```

Now we are fully handling the logical implications of an event log reorg!

```{note}
Please see [the Geth documentation](https://geth.ethereum.org/docs/interacting-with-geth/rpc/pubsub#logs)
for more information about how event log reorgs work
```

## Worker Events

If you have heavier resources you want to load during startup,
or want to initialize things per-worker like database connections,
you can add a _worker startup function_ like so:

```python
@bot.on_worker_startup()
def handle_on_worker_startup(state):
    # Connect to DB, set initial state, etc
    ...

@bot.on_worker_shutdown()
def handle_on_worker_shutdown(state):
    # cleanup resources, close connections cleanly, etc
    ...
```

This function takes a parameter `state` that you can use for storing the results
of your startup computation or resources that you have provisioned.
**This is different from `bot.state`**, and will make it accessible via `taskiq.Context` dependency injection in other tasks.

```{important}
This is not a Silverback native feature, but rather a feature available from the TaskIQ library that Silverback uses.

See the [Taskiq Documentation](https://taskiq-python.github.io/guide/state-and-deps.html) to learn more about it.
```

This feature is useful for ensuring that your workers (specifically when using [Distributed Execution](#distributed-execution))
have the resources necessary to properly handle any updates you want to make in your handler functions,
such as connecting to the Telegram API, an SQL or NoSQL database connection, or something else.

Giving each worker it's own connection to a database or API could **dramatically speed up** the use of contested resources:

```py
@bot.on_worker_startup()
async def connect_db(state):
    state.db = await Connection("posgres://localhost").__aenter__()


@bot.on_(Token.Transfer)
async def store_token(log, context: Annotated[Context, TaskiqDepends()]):
    token = await context.state.db.get(log.contract_address)
    token.balances[log.sender] -= log.amount
    token.balances[log.receiver] += log.amount
    await context.state.db.add(token)
    await context.state.db.commit()


@bot.on_worker_shutdown()
async def handle_on_worker_shutdown(state):
    await state.db.__aexit__()

```

```{important}
The worker startup/shutdown functions will run on **every worker process** (N times for N workers).
```

## Distributed Execution

Using only the `silverback run ...` command in the default configuration executes everything in one process,
and the job queue is ke-pt completely in-memory with a shared state.
This is very useful for local testing purposes, but in some high-volume environments,
you may want to deploy your Silverback bot in a "distributed configuration",
using multiple processes to handle the messages in parallel in order to acheive a higher throughput rate.

Operating in this mode, there are two components: the client and the workers.
The client handles triggering Silverback events (listening for blocks and contract event logs)
and then creates jobs for the workers to process in an asynchronous manner using a "broker".

For this to work, you must configure a [TaskIQ broker](https://taskiq-python.github.io/guide/architecture-overview.html#broker)
capable of distributed processing, such as ZeroMQ or Redis.
Additonally, it is highly suggested you should also configure a
[TaskIQ result backend](https://taskiq-python.github.io/guide/architecture-overview.html#result-backend)
in order to process and store the results of executing tasks,
otherwise you will not collect Metrics or be able to execute any Metric Callbacks.

```{note}
Without configuring a result backend,
Silverback may not work as expected since all your tasks will now suddenly return `None` instead of the actual result.
```

For instance, with [`taskiq_redis`](https://github.com/taskiq-python/taskiq-redis) you could do something like this to configure silverback:

```bash
pip install taskiq-redis
export SILVERBACK_BROKER_CLASS="taskiq_redis:ListQueueBroker"
export SILVERBACK_BROKER_KWARGS='{"queue_name": "taskiq", "url": "redis://127.0.0.1:6379"}'
export SILVERBACK_RESULT_BACKEND_CLASS="taskiq_redis:RedisAsyncResultBackend"
export SILVERBACK_RESULT_BACKEND_URI="redis://127.0.0.1:6379"
```

Then you should start the worker process with 2 worker subprocesses:

```bash
silverback worker -w 2 --network :mainnet
```

Finally, run the client via:

```bash
silverback run --network :mainnet
```

```{important}
Run the client **after** running the workers, otherwise there will be a delay in startup.
```

After the startup sequence, the client will submit tasks on triggers to the 2 worker subprocesses,
and all task queue and results data will be go through Redis back to the client once the workers complete them.

## Running Containers with Local Keyfiles

While not a recommended best practice, it is possible to "attach" your keyfiles and use them inside of a containerized bot.
You can do this using the "volumes" feature of [`docker`](https://docs.docker.com/engine/storage/volumes/)
or [`podman`](https://docs.podman.io/en/latest/markdown/podman-volume.1.html).

The basic idea is to add your local `~/.ape/accounts` folder into your container's runtime environment,
and then use the [Ape automation documentation](https://docs.apeworx.io/ape/stable/userguides/accounts#automation)
to set up non-interactive decryption of the relevant account using the corresponding alias you have supplied via `--account`.

A full example would look like the following:

```sh
docker run \
    --volume ~/.ape/accounts:/home/harambe/.ape/accounts \
    --env APE_ACCOUNTS_<alias>_PASSPHRASE=... \
    ghcr.io/image/name -- run --network ... --account <alias>
```

```{warning}
This is not a recommended best practice, and relies on understanding the volume feature of your runtime tool.

By sharing your filesystem with the docker container, you could allow a container to **read your keyfiles**
shared from your local environemnt and allow decrypting them.

Running a container image that **is not your own** could have drastic impacts on any of these connected wallets,
including **total loss of funds**.
Use this technique only if you trust the source of the container image.
```

## Cluster Access

Sometimes, there are scenarios where you want to create a bot that has the capability to control other bots.
This might make sense if you have one type of bot that monitors a particular contract instance to interact with it,
and then another type of bot that monitors the Factory contract which can produce more instances.
Another example is if you want a bot that can monitor the performance of other bots in your cluster,
and then be able to stop any one of them if it discovers any issues while monitoring it.

```py
@bot.on_(factory.NewPool)
async def new_arb_bot(log):
    vg = bot.cluster.new_variable_group(
        name=f"{log.token0}-{log.token1}-pool",
        variables={"POOL": log.pool_address},
    )
    bot = bot.cluster.new_bot(
        name=f"{log.token0}-{log.token1}-arb",
        ...,
        environment=[vg.name, ...],
    )
```

Creating this type of setup allows a sort of "agentic scaling" where you can quickly scale up your bot cluster's
on-chain "footprint" and cover more ground by having multiple other bots handling situations you encounter.
This also can more effectively distribute the cluster load when you have large quantities of events to be handled,
and need to dynamically scale in order to add more capacity in response to on-chain conditions.

```{important}
Testing cluster access is not practical without deploying on the [Silverback Platform](./platform.html).
The cluster implements additional authorization checks based on the `--cluster-access` flag provided
with the [`silverback cluster bots new`](../commands/cluster#silverback-cluster-bots-new)
and [`silverback cluster bots update`](../commands/cluster#silverback-cluster-bots-update) commands.
```

_Added in Silverback SDK [v0.7.35](https://github.com/ApeWorX/silverback/releases/tag/v0.7.35)_
_Support added in Silverback Cluster [v0.7.0](https://silverback.apeworx.io/changelog#cluster-v0-7-0)_
