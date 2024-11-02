# Developing Bots

In this guide, we are going to show you more details on how to build an bot with Silverback.

## Prerequisites

You should have a python project with Silverback installed.
You can install Silverback via `pip install silverback`

## Project structure

There are 3 suggested ways to structure your project. In the root directory of your project:

1. Create a `bot.py` file. This is the simplest way to define your bot project.

2. Create a `bots/` folder. Then develop bots in this folder as separate scripts (Do not include a __init__.py file).

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

```py
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

```py
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

```
from ape import Contract

TOKEN = Contract(<your token address here>)

@bot.on_(TOKEN.Transfer)
def handle_token_transfer_events(transfer):
    ...
```

Inside of `handle_token_transfer_events` you can define any logic that you want to handle each new `transfer` event that gets emitted by `TOKEN.Transfer` detected by the silverback client.
Again, you can return any serializable data structure from this function and that will be stored in the results database as a trackable metric for the execution of this handler.
Any errors you raise during this function will get captured by the client, and recorded as a failure to handle this `transfer` event log.

## Startup and Shutdown

### Worker Events

If you have heavier resources you want to load during startup, or want to initialize things like database connections, you can add a worker startup function like so:

```py
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

It's import to note that this is useful for ensuring that your workers (of which there can be multiple) have the resources necessary to properly handle any updates you want to make in your handler functions, such as connecting to the Telegram API, an SQL or NoSQL database connection, or something else.  **This function will run on every worker process**.

*New in 0.2.0*: These events moved from `on_startup()` and `on_shutdown()` for clarity.

#### Worker State

The `state` variable is also useful as this can be made available to each handler method so other stateful quantities can be maintained for other uses.  Each distributed worker has its own instance of state.

To access the state from a handler, you must annotate `context` as a dependency like so:

```py
from typing import Annotated
from taskiq import Context, TaskiqDepends

@bot.on_(chain.blocks)
def block_handler(block, context: Annotated[Context, TaskiqDepends()]):
    # Access state via context.state
    ...
```

### Bot Events

You can also add an bot startup and shutdown handler that will be **executed once upon every bot startup**.  This may be useful for things like processing historical events since the bot was shutdown or other one-time actions to perform at startup.

```py
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

*Changed in 0.2.0*: The behavior of the `@bot.on_startup()` decorator and handler signature have changed.  It is now executed only once upon bot startup and worker events have moved on `@bot.on_worker_startup()`.

## Bot State

Sometimes it is very useful to have access to values in a shared state across your workers.
For example you might have a value or complex reference type that you wish to update during one of your tasks, and read during another.
Silverback provides `bot.state` to help with these use cases.

For example, you might want to pre-populate a large dataframe into state on startup, keeping that dataframe in sync with the chain through event logs,
and then use that data to determine a signal under which you want trigger transactions to commit back to the chain.
Such an bot might look like this:

```py
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

```py
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

### Distributed Execution

Using only the `silverback run ...` command in a default configuration executes everything in one process and the job queue is completely in-memory with a shared state.
In some high volume environments, you may want to deploy your Silverback bot in a distributed configuration using multiple processes to handle the messages at a higher rate.

The primary components are the client and workers.  The client handles Silverback events (blocks and contract event logs) and creates jobs for the workers to process in an asynchronous manner.

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
