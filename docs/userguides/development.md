# Developing Bots

In this guide, we are going to show you more details on how to build an bot with Silverback.

## Project structure

There are 3 suggested ways to structure your bot project.
In the root directory of your project, do one of the following:

1. Create a `bot.py` file. This is the simplest way to define your bot project and is suggested if the bot implementation is the entire project you wish to build. You can then run it without adding any name to your command. (e.g. `silverback run --network ...`)

2. Create a `bots/` folder. You can add multiple bots to this folder as separate scripts. This is the suggested way if the bots fit into a larger system or protocol design monorepo. You would need to add the selected bot's name in your commands. (e.g. `silverback run example --network ...`)

3. Create a `bot/` python module, with a `__init__.py` in it. This is recommended if you have significant complex logic in your bot and wish to modularize more code under `bot/`. You will be able to run it without adding any name to your command. (e.g. `silverback run --network ...`)

The `silverback` cli automatically searches for python scripts in these specific locations relative to the root of your project.

```{note}
If you have multiple bots, we suggest using the `bots/` folder approach as it easily supports multi-bot workflows.
Silverback will automatically register files in this folder as separate bots that can be run via the `silverback run` command.
This is especially useful for simulating a more complex protocol you are developing which requires multiple roles
(that you wish to publish as bots).

Treat this folder like a scripts folder: do not include an `__init__.py` in it.
```

```{important}
Name your `SilverbackBot` instance `bot`.
Silverback automatically searches for this object name when running.
If you do not name it `bot`, ensure you add `:<instance name>` to your command.
(e.g. `silverback run my_bot:my_instance --network ...`)
```

## Bot Structure

Creating a Silverback Bot is easy, simply import and initialize the `silverback.SilverbackBot` class:

```python
from silverback import SilverbackBot

bot = SilverbackBot()
```

The `SilverbackBot` class automatically handles state and configuration.
Through this class we can hook up "event handlers",
which are custom Python functions that are called each time the associated event occurs.

```{important}
Initializing the `SilverbackBot` class creates a network connection using the local Ape configuration,
making it easy to add a Silverback bot to your Ape project.
It is required to put any global logic which requires a network connection
(such as loading contracts using a connected explorer) after initializing this class.
```

By default a bot has no configured event handlers, so it won't be very useful.
This is where adding event handlers is useful via the `bot.on_` method.
This method lets us specify blockchain events that we want to handle with custom Python logic.

### New Block Events

To add a handler that triggers whenever the connected network produces a new block,
you need to do the following:

```python
from ape import chain
from silverback import SilverbackBot


bot = SilverbackBot()


@bot.on_(chain.blocks)
def handle_new_block(block):
    ...  # Define your logic here
```

Inside of `handle_new_block` you can define any logic that you need to handle each new `block` created by the network.
Any errors you raise during this function will get captured by the client, and recorded as a failure to handle this `block`.

```{important}
Listening for new blocks is susceptible to chain re-organizations (aka "re-orgs").
See [Handling Reorgs](./advanced.html#handling-reorgs) for more guidance on dealing with them.
```

```{note}
If needed, you can have multiple handlers that trigger on new blocks.
Just add them as a new decorated function.
```

### New Event Logs

Similarly to new blocks, you can handle when event logs emitted by a contract by adding an event log handler:

```python
from ape import Contract
from silverback import SilverbackBot


bot = SilverbackBot()
TOKEN = Contract("<token address>")


@bot.on_(TOKEN.Transfer)
def handle_token_transfer(log):
    ...  # Define your logic here
```

Inside of `handle_token_transfer` you can define any logic that you need to handle each new `Transfer` log emitted by `TOKEN`.
Any errors you raise during this function will get captured by the client, and recorded as a failure to handle this `Transfer` log.

```{important}
Listening for contract event logs is susceptible to chain re-organizations (aka "re-orgs").
See [Handling Reorgs](./advanced.html#handling-reorgs) for more guidance on dealing with them.
```

```{note}
If needed, you can have multiple handlers that trigger on new event logs.
Just add them as a new decorated function.
```

#### Event Log Filters

You can also filter event logs by using event parameters.
For example, if you only want to trigger on `Transfer` logs that represent a "burn"
(a transfer to the zero address according to the ERC20 specification), then you can do:

```python
from ape.utils import ZERO_ADDRESS
...


@bot.on_(USDC.Transfer, to=ZERO_ADDRESS)
def handle_burn(log):
    ...  # Define your logic here
```

In case an event parameter has the name of a Python keyword, we also support filtering by dict:

```python
@bot.on_(USDC.Transfer, filter_args={"from": ZERO_ADDRESS})
def handle_burn(log):
    ...  # Define your logic here
```

```{warning}
Using filter args performs matching using **the loaded contract instance's ABI**,
which could be different depending on your Ape environment and how you loaded the contract originally.

When in doubt, delete the corresponding entries from `~/.ape/<ecosystem>/<network>/**/<address>.json`.
This is especially important when considering containerizing your bot for cloud use.
```

### Cron Tasks

You may also want to run some handlers according to a schedule,
either for efficiency reasons or just that the task is not related to any blockchain activity.
You can do that easily with the `@bot.cron` task decorator:

```python
@bot.cron("0 * * * *")
def every_hour(time):
    ...  # Define your logic here
```

```{important}
The function is called by giving a `datetime.datetime` object representing the "current" time.
Silverback bots can be executed in a "historical mode" (for backtesting purposes),
allowing you to test functionality of your bot by mimicking past operation on historical data.
It is important to use this argument for any time-specific operation in your handler,
and not use context-dependent functionality like `datetime.now()` which could bias your response.
```

```{note}
For more information on desiging crons, see the
[linux handbook for crontab syntax](https://linuxhandbook.com/crontab/#understanding-crontab-syntax)
or the [crontab.guru](https://crontab.guru) generator.
```

### Defining Metrics

Silverback has a built-in metrics collection system which lets you capture measurements of important metrics using your bot,
which can assist you in a variety of tasks such as debugging or monitoring it's performance.
To capture a measurement, simply return boolean values or numeric values from your function handlers,
or use any of our supported [Datapoint types](../methoddocs/types.html#silverback.types.Datapoint).
The series of metric measurements will also be captured and appended to a timeseries, when enabled to run in this mode.

```{important}
Metrics are tracked globally across your bot.
If you generate metric measurements in two different function handlers using the same metric name,
they will both be appended to the same metric timeseries.
```

When you return a datapoint measurement directly, the metric's name is the name of the function handler that produced it.
However, when you return a dictionary containing multiple measurements,
the string key corresponds to metric's name you are capturing a datapoint for.

For example, both of the following handlers `handlerA` and `handlerB` generate the `block_time` metric,
along with the `block_time` handler which also generates a matching metric of the same name (because it does not return a dict):

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

This can be really useful if you have a complex metric which might occur under several conditions.

#### Metric Callbacks

A special feature of Silverback's metrics system is the ability to trigger tasks to execute when new measurements are produced.
For example, say that you have a metric `current_price` that is computed every so often using a `cron` task trigger:

```python
@bot.cron("* * * * *")
async def current_price(time):
    current_price = ...
    return current_price
```

You can then define a "metric callback" that is triggered whenever a new measurement of that metric occurs:

```python
@bot.on_metric("current_price")
async def on_current_price(current_price: float):
    ...  # Do something with the price
```

This can be particularly handy for definining conditions that trigger whenever a metric has been measured conditionally.
Let's say that you want to measure a metric `pool_delta` only when a trade is made in another handler:

```python
@bot.on_(pool.Swap)
async def check_pool_balance(log):
    if log.reserve0 > log.reserve1:
        return dict(pool_delta=log.reserve0 - log.reserve1)
```

Then you want to trigger off updates to that metric, but only if a measurement is _larger than a threshold_.
We can use `gt=<threshold>` to do this:

```python
@bot.on_metric("pool_delta", gt=MIN_DELTA)
async def perform_rebalance(pool_delta: int):
    pool.rebalance(amount=pool_delta, ..., sender=bot.signer)
```

```{note}
`.on_metric` supports 6 different value comparisons (`gt`, `ge`, `lt`, `le`, `ne`, and `eq`).
When multiple comparisons are present, they are treated as a logical AND (meaning they must all be true for the task to execute).
If you need a separate task to execute on different compound conditions, simply define extra handlers.
```

### Bot Lifecycle Events

In addition to "runtime triggers" (new blocks, event logs, crons, and metric callback triggers),
we also have the ability to trigger on bot startup and shutdown.

```{note}
Bot lifecycle and bot runtime triggers are handled distinctly and can be trusted not to execute at the same time.
```

#### Bot Startup and Shutdown

You can add a handler that you want to be **executed only once** upon every bot startup or shutdown lifecycle event.
This might be useful for things like backfilling historical data to prime the bot's operation,
or performing a final action like sending a notification or transaction to undo a potentially dangerous offline state.

```python
@bot.on_startup()
def handle_on_startup(startup_state):
    # Process missed events, etc
    # process_history(start_block=startup_state.last_block_seen)
    # ...or startup_state.last_block_processed


@bot.on_shutdown()
def handle_on_shutdown():
    # Record final state, send a de-leverage transaction, etc.
```

```{important}
`.on_startup()` handlers will **only trigger once** during the startup sequence,
and any failures will cause the bot not to transition into the runtime mode.
```

```{note}
`.on_shutdown()` handlers will **only trigger once** during the shutdown sequence,
which only executes if the bot was previously in the runtime mode.
Any failures that occur will **not** impact the shutdown of the bot in any way.
```

### Bot State

Sometimes it is very useful to have access to values in a shared state across your bot.
For example you might have a value that you wish to update during execution of one of your handlers,
and then read during the execution of another.
Silverback provides `bot.state` to help with these use cases.

For example, you might want to pre-populate a large dataframe into state using a startup handler,
keep that dataframe in sync with the chain through several event log handlers,
and then process that data to produce a custom metric every couple of minutes,
which may trigger sending a transaction.

Such an bot might look like this:

```python
@bot.on_startup()
def load_df(startup_state):
    bot.state.df = contract.MyEvent.query(..., start_block=startup_state.last_block_processed)
    ...  # Do some further processing on `bot.state.df`


@bot.on_(contract.MyEvent)
def update_df(log):
    bot.state.df.loc[-1] = ...  # Add a row using stuff from `log`


@bot.cron("0 * * * *")
def measure_metric(time):
    metric = ...  # Use `bot.state.df` to produce a metric
    return {"metric": metric}


@bot.on_metric("metric", gt=MIN_THRESHOLD)
def use_table(metric):
    # Trigger your bot to send a transaction from `bot.signer`
    contract.myMethod(..., sender=bot.signer)
```

```{warning}
While you can use `bot.state` to store any python variable type,
note that the item is not networked nor threadsafe,
so it is not recommended to have multiple tasks write to the same value in state at the same time.
```

### Signing Transactions

If configured, your bot with have `bot.signer` which is an Ape account that can sign arbitrary transactions you ask it to.
To learn more about signing transactions with Ape,
see the [documentation](https://docs.apeworx.io/ape/stable/userguides/transactions.html).

```{important}
For local development, you can use keyfile accounts for automated signing.
Silverback will prompt you to unlock the keyfile and then set "autosign" mode, which will sign all transactions your bot triggers.
See [this guide](https://docs.apeworx.io/ape/stable/userguides/accounts.html#automation) to learn more about autosign mode.
```

```{danger}
For cloud deployment, it is **not** recommended to use keyfile accounts.
Instead, it is recommended to use a dedicated cloud signer plugin such as [`aws`](https://github.com/ApeWorX/ape-aws).

Using keyfiles in a cloud setting could lead to **permanent fund loss** if the keyfile (and it's passphrase) leak,
and it is also not very helpful for others wishing to run your bots.
Using a dedicated service-based signer plugin adds extra security to your deployment,
and makes it possible for anyone to run your bot by bringing **their managed own keys**.
```

#### Managing nonces

Since Silverback allows handling many events in parallel,
it can allow you to submit multiple transactions in a short timespan
(in fact, prior to successful confirmation of previously broadcasted transactions)
It may become vital to do "nonce management" in order to ensure that you are not producing transactions
that might conflict with one another.

The `bot.nonce` variable tracks the last-used nonce of the `bot.signer`,
incrementing it every time a new transaction is signed _during the bot's operation_.
By using this variable (via `nonce=bot.nonce` in your transactions),
you can ensure that you do not produce transactions with conflicting nonces,
even at a very high rate of parallel transaction creation.
Do this instead of using `bot.signer.nonce`, which is the default behavior when the `nonce=` transaction kwarg is omitted.

```{note}
The value of `bot.nonce` is the maximum between the internally-stored "last-used nonce",
and the value given by `eth_getNonce` RPC method, so it should never get "out of sync" in practice.
```

```{warning}
Make sure to use appropiate gas pricing in order to prevent chains of multiple transactions
from becoming "stuck", because an earlier broadcasted transaction was under-priced.
```

```{danger}
Do *not* use the same account on the same network at the same time as the one in use by your bot,
as this could lead to extremely undesirable behavior, stuck transactions, transaction failures,
or **loss of funds**.
```

### Managed Parameters

Silverback has support for defining numeric/boolean values in `bot.state` that can be updated during runtime operation of the bot.
This is very useful for implementing features in your bot such as operational modes, parametrized signal processing algorithms that can be adjusted in real-time, or otherwise allowing the operational behavior of your bot to become configurable above and beyond what is possible through simple environment variables.
Further, these parameters are backed up and stored through Silverback's state snapshotting feature, which means they retain their changes during bot resets and new deployments on the [Silverback Platform](./managing.html).

To use this feature in your bot, you will use the `bot.add_parameter` function to define your parameter's name and default value (the value that is loaded in `bot.state.<your_parameter>` if no value is detected in the state snapshot on loading).
Here's an example:

```py
...

bot = SilverbackBot()

bot.add_parameter("my_parameter", default=0.1)
```

You can then access this value inside of any bot handler functions via `bot.state.my_parameter` or `bot.state["my_parameter"]`:

```py
...

@bot.on_(chain.blocks)
async def measure_something(block):
    bot.state.measurement *= bot.state.my_parameter * block.base_fee

...
```

```{warning}
Since parameters are loaded into `bot.state`, they are not accessible outside of your bot's handler functions.
It is also recommended that you do not modify them dynamically, although that behavior is allowed.
```

```{note}
Parameter definitions are defined under `bot.parameters` but do not contain their current value, which must be accessed through `bot.state`.
However, this can be useful if you need to access your parameter's properties such as the default value.
```

If you want to test modifying your parameter when testing locally, first set your model up using [Distributed Execution](#distributed-execution), and then use the [`silverback set-param`](../commands/run.html#silverback-set-param) command.

## Running your Bot

Once you have programmed your bot,
it's really useful to be able to run it locally and validate that it does what you expect it to do.
To run your bot locally, we have included the cli command [`run`](../commands/run)
that takes care of connecting to the proper network, configuring signers (using your local Ape accounts),
and starting up the bot runtime and worker clients.

```sh
# Run `bot.py` on the Ethereum Sepolia testnet, with your own signer:
$ silverback run --network :sepolia --account acct-name
```

```{note}
`bot:bot` is not required for silverback run if you follow the suggested folder structure at the start of this page,
you can just omit it as an argument.
```

It's important to note that signers are optional, if not configured in the bot then `bot.signer` will be `None`.
You can use this in your bot to enable a "test execution" mode, something like this:

```python
@bot.on_metric("metric-name", gt=THRESHOLD)
def execute_trade(metric):
    if bot.signer:
        ... # Execute a transaction via `sender=bot.signer`

    else:
        ... # simulate what the transaction *would* have done
```

```{warning}
If you configure your bot to use a signer, that signer will sign anything given to it,
so remember that you can lose substational amounts of funds if you deploy this to a production network.

Always test your bots throughly before deploying, and always use a dedicated key for production use with proper safety precautions.
```

```{danger}
It is highly suggested to use a dedicated cloud signer plugin,
such as [`ape-aws`](https://github.com/ApeWorX/ape-aws) for signing transactions in a cloud environment.

Use segregated keys and limit your risk by controlling the amount of funds that key has access to at any given time.
```

### Runtime Exceptions

It is important to note that when running your bot with Silverback,
a failure in one of your tasks while running **does not necessarily cause it to shutdown immediately**.
This is done to support handling occasional failures and unexpected scenarios that might happen during the runtime of your bot in practice.

The way it works is that during runtime (after the startup phase has completed successfully),
the silverback runner will track the number of failures that occurs in _any_ task;
and, if there is more than the configured amount of exceptions occuring across all of your tasks,
only then will it Halt the runtime mode and stop your bot from running (as well as trigger your shutdown handlers).

You can configure this behavior when running with the [`silverback run ...`](../commands/run#silverback-run)
command by changing the value of the `--max-exceptions` option.
A higher number will take more failures in order to trigger a complete shutdown of the bot,
whereas a lower number will make it more sensistive to intermittent failures you are likely to find in production use.
The default is chosen as a good balance between error sensitivity and operational robustness.

```{warning}
Any failures that occur in **any of your startup tasks** (including system-level startup tasks internal to the SDK)
will cause an immediate failure, and prevent the bot from transitioning into runtime mode,
where failure persistence monitoring becomes active.
```

This error tracking behavior in the runtime mode will occur handling any Python exception that your code is likely to raise.
However, by raising a [`CircuitBreaker`](../methoddocs/exceptions#silverback.exceptions.CircuitBreaker) exception
(or a subclass of it), you can cause your bot to **immediately shutdown** in response to an application-specific fault.

This might be useful if you know of a situation or invariant that you want to make sure to maintain
during the operation of your bot no matter what, or if you detect that you no longer wish to run the
bot any more for any desired reason (and want to handle it programmatically).

```{note}
Any failures detected during shutdown tasks do not prevent the execution of any other shutdown tasks
(including system-level shutdown tasks internal to the SDK).
```

Lastly, you can insert a request during runtime to kill your bot manually by performing `ctrl+C`,
or by sending a SIGTERM or SIGINT signal to the runtime process (from your task manager or orchestration runtime).
This will trigger the same halting behavior immediately triggering your bot to move into the shutdown mode,
and execute all shutdown tasks before exiting the process completely.

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
