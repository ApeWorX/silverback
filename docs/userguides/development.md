# Developing Bots

In this guide, we are going to show you more details on how to build an bot with Silverback.

## Prerequisites

You should have a python project with Silverback installed.
You can install Silverback via `pip install silverback`

## Project structure

There are 3 suggested ways to structure your project. In the root directory of your project:

1. Create a `bot.py` file. This is the simplest way to define your bot project.

2. Create a `bots/` folder. Then develop bots in this folder as separate scripts (Do not include a `__init__.py` file).

3. Create a `bot/` folder with a `__init__.py` file that will include the instantiation of your `SilverbackBot()` object.

The `silverback` cli automatically searches for python scripts to run as bots in specific locations relative to the root of your project.
It will also be able to detect the scripts inside your `bots/` directory and let you run those by name (in case you have multiple bots in your project).

If `silverback` finds a module named `bot` in the root directory of the project, then it will use that by default.

```{note}
It is suggested that you create the instance of your `SilverbackBot()` object by naming the variable `bot`, since `silverback` will autodetect that variable name when loading your script file.
```

Another way you can structure your bot is to create a `bot` folder and define a runner inside of that folder as `__init__.py`.

If you have a more complicated project that requires multiple bots, naming each bot their own individual name is okay to do, and we encourage you to locate them under the `bots/` folder relative to the root of your project.
This will work fairly seamlessly with the rest of the examples shown in this guide.

To run a bot, as long as your project directory follows the suggestions above by using a `bot` module, you can run it easily with:

```bash
silverback run --network your:network:of:choice
```

If your bot's module name is `example.py` (for example), you can run it like this:

```bash
silverback run example --network your:network:of:choice
```

If the variable that you call the `SilverbackBot()` object is something other than `bot`, you can specific that by adding `:{variable-name}`:

```bash
silverback run example:my_bot --network your:network:of:choice
```

We will automatically detect all scripts under the `bots/` folder automatically, but if your bot resides in a location other than `bots/` then you can use this to run it:

```bash
silverback run folder.example:bot --network your:network:of:choice
```

Note that with a `bot/__init__.py` setup, silverback will also autodetect it, and you can run it with:

```bash
silverback run --network your:network:of:choice
```

```{note}
It is suggested that you develop your bots as scripts to keep your deployments simple.
If you have a deep understanding of containerization, and have specific needs, you can set your bots up however you'd like, and then create your own container definitions for deployments to publish to your reqistry of choice.
For the most streamlined experience, develop your bots as scripts, and avoid relying on local packages
(e.g. do not include an `__init__.py` file inside your `bots/` directory, and do not use local modules inside `bots/` for reusable code).
If you follow these suggestions, your Silverback deployments will be easy to use and require almost no thought.
```

## Creating a Bot

Creating a Silverback Bot is easy, to do so initialize the `silverback.SilverbackBot` class:

```python
from silverback import SilverbackBot

bot = SilverbackBot()
```

The `SilverbackBot` class handles state and configuration.
Through this class, we can hook up event handlers to be executed each time we encounter a new block or each time a specific event is emitted.
Initializing the bot creates a network connection using the Ape configuration of your local project, making it easy to add a Silverback bot to your project in order to perform automation of necessary on-chain interactions required.

However, by default an bot has no configured event handlers, so it won't be very useful.
This is where adding event handlers is useful via the `bot.on_` method.
This method lets us specify which event will trigger the execution of our handler as well as which handler to execute.

## New Block Events

To add a block handler, you will do the following:

```python
from ape import chain

@bot.on_(chain.blocks)
def handle_new_block(block):
    ...
```

Inside of `handle_new_block` you can define any logic that you want to handle each new `block` detected by the silverback client.
You can return any serializable data structure from this function and that will be stored in the results database as a trackable metric for the execution of this handler.
Any errors you raise during this function will get captured by the client, and recorded as a failure to handle this `block`.

## New Event Logs

Similarly to blocks, you can handle events emitted by a contract by adding an event handler:

```python
from ape import Contract

TOKEN = Contract(<your token address here>)

@bot.on_(TOKEN.Transfer)
def handle_token_transfer_events(transfer):
    ...
```

Inside of `handle_token_transfer_events` you can define any logic that you want to handle each new `transfer` event that gets emitted by `TOKEN.Transfer` detected by the silverback client.
Again, you can return any serializable data structure from this function and that will be stored in the results database as a trackable metric for the execution of this handler.
Any errors you raise during this function will get captured by the client, and recorded as a failure to handle this `transfer` event log.

### Event Log Filters

You can also filter event logs by event parameters.
For example, if you want to handle only `Transfer` events that represent a burn (a transfer to the zero address):

```python
@bot.on_(USDC.Transfer, to="0x0000000000000000000000000000000000000000")
def handle_burn(log):
    return {"burned": log.value}
```

In case an event parameter has a name that is an illegal keyword, we can also support a dictionary syntax:

```python
@bot.on_(USDC.Transfer, filter_args={"from":"0x0000000000000000000000000000000000000000"})
def handle_burn(log):
    return {"burned": log.value}
```

## Cron Tasks

You may also want to run some tasks according to a schedule, either for efficiency reasons or just that the task is not related to any chain-driven events.
You can do that with the `@cron` task decorator.

```python
@bot.cron("* */1 * * *")
def every_hour():
    ...
```

For more information see [the linux handbook section on the crontab syntax](https://linuxhandbook.com/crontab/#understanding-crontab-syntax) or the [crontab.guru](https://crontab.guru/) generator.

## Defining Metrics

Silverback has a built-in metrics collection system which can capture measurements made by your bots, which can assist you in debugging or performance monitoring.
To capture a measurement of a metric datapoint, simply return boolean values or numeric data from your function handlers, or use any of our defined [Datapoint types](../methoddocs/types).
When you return a datapoint measurement directly, the datapoint is stored using the label of it's function handler to append to a timeseries for the metric.
When you return a dictionary containing multiple measurements, the string key corresponds to label of the metric you are capturing a datapoint for.

Note that metric labels are tracked globally across your bot.
If you generate metrics in two different function handlers that have the same string keys in the dictionary, they will both be appended to the same metric timeseries.

For example, both of the following handlers `handlerA` and `handlerB` generate the `block_time` metric, along with the `block_time` handler which also generates a matching metric of the same name (because it does not return a dict):

```python
@bot.on_(my_contract.MyEvent)
async def handlerA(log):
    return dict(block_time=log.timestamp)

@bot.cron("* * * * *")
async def handlerB(time):
    return {"block_time": int(time.timestamp())}

@bot.on_(chain.blocks)
async def block_time(block):
    return block.timestamp
```

### Metric Callbacks

A special feature of Silverback's metrics system is the ability to trigger tasks to execute when your metrics are produced.
For example, say that you have a metric `current_price` that is computed every so often using a `cron` task trigger:

```python
@bot.cron("* * * * *")
async def current_price(time):
    current_price = ...
    return current_price
```

You can then define a metric callback that is triggered whenever that metric is produced:

```python
@bot.on_metric("current_price")
async def on_current_price(current_price: float):
    ...  # Do something with the price
```

This can be particularly handy for definining conditions that trigger whenever a metric is conditionally produced.
Let's say that you want to record a metric `pool_delta` whenever a particular trade is made using an event trigger:

```python
@bot.on_(pool.Swap)
async def check_pool_balance(log):
    if log.reserve0 > log.reserve1:
        return dict(pool_delta=log.reserve0 - log.reserve1)
```

Then you want to trigger off of that metric, but only if it's value is _larger than a threshold_.
We can use `gt=<threshold>` to do this:

```python
@bot.on_metric("pool_delta", gt=MIN_DELTA)
async def perform_rebalance(pool_delta: int):
    pool.rebalance(amount=pool_delta, ..., sender=bot.signer)
```

```{note}
`.on_metric` supports 6 different value comparisons (`gt`, `ge`, `lt`, `le`, `ne`, and `eq`).
When multiple comparisons are present, they are treated as a logical AND (meaning they must all be true for the task to execute).
If you need a separate task to execute on different compound conditions, simply define extra tasks.
```

## Startup and Shutdown

### Worker Events

If you have heavier resources you want to load during startup, or want to initialize things like database connections, you can add a worker startup function like so:

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

This function comes a parameter `state` that you can use for storing the results of your startup computation or resources that you have provisioned.

It's import to note that this is useful for ensuring that your workers (of which there can be multiple) have the resources necessary to properly handle any updates you want to make in your handler functions, such as connecting to the Telegram API, an SQL or NoSQL database connection, or something else. **This function will run on every worker process**.

_New in 0.2.0_: These events moved from `on_startup()` and `on_shutdown()` for clarity.

#### Worker State

The `state` variable is also useful as this can be made available to each handler method so other stateful quantities can be maintained for other uses. Each distributed worker has its own instance of state.

To access the state from a handler, you must annotate `context` as a dependency like so:

```python
from typing import Annotated
from taskiq import Context, TaskiqDepends

@bot.on_(chain.blocks)
def block_handler(block, context: Annotated[Context, TaskiqDepends()]):
    # Access state via context.state
    ...
```

### Bot Events

You can also add an bot startup and shutdown handler that will be **executed once upon every bot startup**. This may be useful for things like processing historical events since the bot was shutdown or other one-time actions to perform at startup.

```python
@bot.on_startup()
def handle_on_startup(startup_state):
    # Process missed events, etc
    # process_history(start_block=startup_state.last_block_seen)
    # ...or startup_state.last_block_processed
    ...


@bot.on_shutdown()
def handle_on_shutdown():
    # Record final state, etc
    ...
```

_Changed in 0.2.0_: The behavior of the `@bot.on_startup()` decorator and handler signature have changed. It is now executed only once upon bot startup and worker events have moved on `@bot.on_worker_startup()`.

## Bot State

Sometimes it is very useful to have access to values in a shared state across your workers.
For example you might have a value or complex reference type that you wish to update during one of your tasks, and read during another.
Silverback provides `bot.state` to help with these use cases.

For example, you might want to pre-populate a large dataframe into state on startup, keeping that dataframe in sync with the chain through event logs,
and then use that data to determine a signal under which you want trigger transactions to commit back to the chain.
Such an bot might look like this:

```python
@bot.on_startup()
def create_table(startup_state):
    df = contract.MyEvent.query(..., start_block=startup_state.last_block_processed)
    ...  # Do some further processing on df
    bot.state.table = df


@bot.on_(contract.MyEvent)
def update_table(log):
    bot.state.table = ...  # Update using stuff from `log`


@bot.on_(chain.blocks)
def use_table(blk):
    if bot.state.table[...].mean() > bot.state.table[...].sum():
        # Trigger your bot to send a transaction from `bot.signer`
        contract.myMethod(..., sender=bot.signer)
    ...
```

```{warning}
You can use `bot.state` to store any python variable type, however note that the item is not networked nor threadsafe so it is not recommended to have multiple tasks write to the same value in state at the same time.
```

```{note}
Bot startup and bot runtime event triggers (e.g. block or event container) are handled distinctly and can be trusted not to execute at the same time.
```

### Signing Transactions

If configured, your bot with have `bot.signer` which is an Ape account that can sign arbitrary transactions you ask it to.
To learn more about signing transactions with Ape, see the [documentation](https://docs.apeworx.io/ape/stable/userguides/transactions.html).

```{warning}
While not recommended, you can use keyfile accounts for automated signing.
See [this guide](https://docs.apeworx.io/ape/stable/userguides/accounts.html#automation) to learn more about how to do that.
```

### Managing nonces

Since Silverback allows handling many events in parallel, and thus can allow you to submit multiple transactions in a short timespan (in fact, prior to successful confirmation of previously broadcasted transactions), it may become vital to do "nonce management" in order to ensure that you are not producing transactions that might conflict with one another.
The `bot.nonce` variable tracks the last-used nonce of the `bot.signer`, incrementing it every time a new transaction is signed _during the bot's operation_.
By using this variable via `nonce=bot.nonce` in your transactions (instead of `bot.signer.nonce`, which is the default behavior when the `nonce=` transaction kwarg is omitted), you can ensure that you do not produce transactions with conflicting nonces, even at a very high rate of parallel transaction creation.

```{note}
The value of `bot.nonce` is the maximum between the internally-stored "last-used nonce", and the value given by RPC method
```

```{warning}
Make sure to use an appropiate gas pricing algorithm in order to prevent your chain of multiple transactions from becoming "stuck" because an earlier broadcasted transaction was under-priced
```

```{warning}
Do *not* use the same account on the same network at the same time as the one in use by your bot, as this could lead to extremely undesirable behavior, stuck transactions, or transaction failures/loss of funds
```

## Running your Bot

Once you have programmed your bot, it's really useful to be able to run it locally and validate that it does what you expect it to do.
To run your bot locally, we have included a really useful cli command [`run`](../commands/run) that takes care of connecting to the proper network, configuring signers (using your local Ape accounts), and starting up the bot client and in-memory task queue workers.

```sh
# Run your bot on the Ethereum Sepolia testnet, with your own signer:
$ silverback run my_bot --network :sepolia --account acct-name
```

```{note}
`my_bot:bot` is not required for silverback run if you follow the suggested folder structure at the start of this page, you can just call it via `my_bot`.
```

It's important to note that signers are optional, if not configured in the bot then `bot.signer` will be `None`.
You can use this in your bot to enable a "test execution" mode, something like this:

```python
# Compute some metric that might lead to creating a transaction
if bot.signer:
    # Execute a transaction via `sender=bot.signer`
else:
    # Log what the transaction *would* have done, had a signer been enabled
```

```{warning}
If you configure your bot to use a signer, and that signer signs anything given to it, remember that you can lose substational amounts of funds if you deploy this to a production network.
Always test your bots throughly before deploying, and always use a dedicated key for production signing with your bot in a remote setting.
```

```{note}
It is highly suggested to use a dedicated cloud signer plugin, such as [`ape-aws`](https://github.com/ApeWorX/ape-aws) for signing transactions in a cloud environment.
Use segregated keys and limit your risk by controlling the amount of funds that key has access to at any given time.
```

### Runtime Exceptions

It is important to note that when running your bot with Silverback,
a failure in one of your tasks while running **does not necessarily cause it to shutdown immediately**.
This is done to support handling occasional failures and unexpected scenarios that might happen during the runtime of your bot in practice.
The way it works is that during runtime (after the startup phase has completed successfully),
the silverback runner will track the number of failures that occurs in _any_ task;
and, if there is more than the configured amount of exceptions occuring across all of your tasks,
only then will trigger a runtime Halt that will stop your bot from running (and trigger your shutdown tasks).

You can configure this behavior when running with the [`silverback run ...`](../commands/run#silverback-run) command by changing the value of the `--max-exceptions` option.
A higher number will take more failures in order to trigger a complete shutdown of the bot,
whereas a lower number will make it more sensistive to intermittent failures you are likely to find in practice.
The default is chosen as a good balance between sensitivity and operational robustness.

```{warning}
Any failures that occur in **any of your startup tasks** (including system-level startup tasks internal to the SDK) will cause an immediate failure,
and prevent the bot from transitioning into runtime mode, where failure persistence becomes active.
```

This error tracking behavior in the runtime mode will occur handling any Python exception that your code is likely to raise.
However, by raising the [`CircuitBreaker`](../methoddocs/exceptions#silverback.exceptions.CircuitBreaker) exception (or a subclass of it),
you can cause your bot to **immediately shutdown** in response to application-specific faults you might detect.
This might be useful if you know of a situation or invariant that you want to make sure to maintain during the operation of your bot no matter what,
or if you detect that you no longer wish to run the bot any more for any desired reason (and want to handle it programmatically).

```{note}
Any failures detected during shutdown tasks do not prevent the execution of any other shutdown tasks
(including system-level shutdown tasks internal to the SDK).
```

Lastly, you can insert a request during runtime to kill your bot manually by performing `ctrl+C`, or by sending a SIGKILL signal to the runtime process.
This will trigger the same halting behavior immediately triggering your bot to move into the shutdown mode, and execute all shutdown tasks before exiting.

### Metrics Collection

To enable collection of metric data into session-based cache files, you need to enable the recording
functionality on [`silverback run`](../commands/run#silverback-run) command via the `--record` flag.
By default, `--record` uses the [`JSONLineRecorder`](../methoddocs/recorder#silverback.recorder.JSONLineRecorder)
class, which journals your bot session's results to disk under timestamped files in
`./.silverback-sessions/<bot name>/<ecosystem>/<network>/`.

To assist you in loading the metrics from these files for things like analyzing them with DataFrame libraries,
use the [`get_metrics`](../methoddocs/recorder#silverback.recorder.get_metrics) function.

You can supply a custom class for recording via the `--recorder <path.to.module:ClassName>` option to the run command,
or by supplying the `SILVERBACK_RECORDER_CLASS=<path.to.module:ClassName>` environment variable.
All recorders should be a subclass of the [`BaseRecorder`](../methoddocs/recorder#silverback.recorder.BaseRecorder)
abstract base class.

### Distributed Execution

Using only the `silverback run ...` command in a default configuration executes everything in one process and the job queue is completely in-memory with a shared state.
In some high volume environments, you may want to deploy your Silverback bot in a distributed configuration using multiple processes to handle the messages at a higher rate.

The primary components are the client and workers. The client handles Silverback events (blocks and contract event logs) and creates jobs for the workers to process in an asynchronous manner.

For this to work, you must configure a [TaskIQ broker](https://taskiq-python.github.io/guide/architecture-overview.html#broker) capable of distributed processing.
Additonally, it is highly suggested you should also configure a [TaskIQ result backend](https://taskiq-python.github.io/guide/architecture-overview.html#result-backend) in order to process and store the results of executing tasks.

```{note}
Without configuring a result backend, Silverback may not work as expected since your tasks will now suddenly return `None` instead of the actual result.
```

For instance, with [`taskiq_redis`](https://github.com/taskiq-python/taskiq-redis) you could do something like this for the client:

```bash
export SILVERBACK_BROKER_CLASS="taskiq_redis:ListQueueBroker"
export SILVERBACK_BROKER_KWARGS='{"queue_name": "taskiq", "url": "redis://127.0.0.1:6379"}'
export SILVERBACK_RESULT_BACKEND_CLASS="taskiq_redis:RedisAsyncResultBackend"
export SILVERBACK_RESULT_BACKEND_URI="redis://127.0.0.1:6379"

silverback run --network :mainnet:alchemy
```

And then the worker process with 2 worker subprocesses:

```bash
export SILVERBACK_BROKER_CLASS="taskiq_redis:ListQueueBroker"
export SILVERBACK_BROKER_KWARGS='{"url": "redis://127.0.0.1:6379"}'
export SILVERBACK_RESULT_BACKEND_CLASS="taskiq_redis:RedisAsyncResultBackend"
export SILVERBACK_RESULT_BACKEND_URI="redis://127.0.0.1:6379"

silverback worker -w 2
```

The client will send tasks to the 2 worker subprocesses, and all task queue and results data will be go through Redis.

## Testing your Bot

TODO: Add backtesting mode w/ `silverback test`

## Deploying your Bot

Check out the [Platform Deployment Userguide](./platform.html) for more information on how to deploy your bot to the [Silverback Platform](https://silverback.apeworx.io).
