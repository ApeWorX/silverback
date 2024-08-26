# Developing Applications

In this guide, we are going to show you more details on how to build an application with Silverback.

## Prerequisites

You should have a python project with Silverback installed.
You can install Silverback via `pip install silverback`

## Creating an Application

Creating a Silverback Application is easy, to do so initialize the `silverback.SilverbackApp` class:

```py
from silverback import SilverbackApp

app = SilverbackApp()
```

The SilverbackApp class handles state and configuration.
Through this class, we can hook up event handlers to be executed each time we encounter a new block or each time a specific event is emitted.
Initializing the app creates a network connection using the Ape configuration of your local project, making it easy to add a Silverback bot to your project in order to perform automation of necessary on-chain interactions required.

However, by default an app has no configured event handlers, so it won't be very useful.
This is where adding event handlers is useful via the `app.on_` method.
This method lets us specify which event will trigger the execution of our handler as well as which handler to execute.

## New Block Events

To add a block handler, you will do the following:

```py
from ape import chain

@app.on_(chain.blocks)
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

@app.on_(TOKEN.Transfer)
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
@app.on_worker_startup()
def handle_on_worker_startup(state):
    # Connect to DB, set initial state, etc
    ...

@app.on_worker_shutdown()
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

@app.on_(chain.blocks)
def block_handler(block, context: Annotated[Context, TaskiqDepends()]):
    # Access state via context.state
    ...
```

### Application Events

You can also add an application startup and shutdown handler that will be **executed once upon every application startup**.  This may be useful for things like processing historical events since the application was shutdown or other one-time actions to perform at startup.

```py
@app.on_startup()
def handle_on_startup(startup_state):
    # Process missed events, etc
    # process_history(start_block=startup_state.last_block_seen)
    # ...or startup_state.last_block_processed
    ...


@app.on_shutdown()
def handle_on_shutdown():
    # Record final state, etc
    ...
```

*Changed in 0.2.0*: The behavior of the `@app.on_startup()` decorator and handler signature have changed.  It is now executed only once upon application startup and worker events have moved on `@app.on_worker_startup()`.

### Signing Transactions

If configured, your bot with have `app.signer` which is an Ape account that can sign arbitrary transactions you ask it to.
To learn more about signing transactions with Ape, see the [documentation](https://docs.apeworx.io/ape/stable/userguides/transactions.html).

```{warning}
While not recommended, you can use keyfile accounts for automated signing.
See [this guide](https://docs.apeworx.io/ape/stable/userguides/accounts.html#automation) to learn more about how to do that.
```

## Running your Application

Once you have programmed your bot, it's really useful to be able to run it locally and validate that it does what you expect it to do.
To run your bot locally, we have included a really useful cli command [`run`](../commands/run) that takes care of connecting to the proper network, configuring signers (using your local Ape accounts), and starting up the application client and in-memory task queue workers.

```sh
# Run your bot on the Ethereum Sepolia testnet, with your own signer:
$ silverback run my_bot:app --network :sepolia --account acct-name
```

It's important to note that signers are optional, if not configured in the application then `app.signer` will be `None`.
You can use this in your application to enable a "test execution" mode, something like this:

```py
# Compute some metric that might lead to creating a transaction
if app.signer:
    # Execute a transaction via `sender=app.signer`
else:
    # Log what the transaction *would* have done, had a signer been enabled
```

```{warning}
If you configure your application to use a signer, and that signer signs anything given to it, remember that you can lose substational amounts of funds if you deploy this to a production network.
Always test your applications throughly before deploying, and always use a dedicated key for production signing with your application in a remote setting.
```

```{note}
It is highly suggested to use a dedicated cloud signer plugin, such as [`ape-aws`](https://github.com/ApeWorX/ape-aws) for signing transactions in a cloud environment.
Use segregated keys and limit your risk by controlling the amount of funds that key has access to at any given time.
```

### Distributed Execution

Using only the `silverback run ...` command in a default configuration executes everything in one process and the job queue is completely in-memory with a shared state.
In some high volume environments, you may want to deploy your Silverback application in a distributed configuration using multiple processes to handle the messages at a higher rate.

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

silverback run "example:app" --network :mainnet:alchemy
```

And then the worker process with 2 worker subprocesses:

```bash
export SILVERBACK_BROKER_CLASS="taskiq_redis:ListQueueBroker"
export SILVERBACK_BROKER_KWARGS='{"url": "redis://127.0.0.1:6379"}'
export SILVERBACK_RESULT_BACKEND_CLASS="taskiq_redis:RedisAsyncResultBackend"
export SILVERBACK_RESULT_BACKEND_URI="redis://127.0.0.1:6379"

silverback worker -w 2 "example:app"
```

The client will send tasks to the 2 worker subprocesses, and all task queue and results data will be go through Redis.

## Testing your Application

TODO: Add backtesting mode w/ `silverback test`

## Deploying your Application

Check out the [Platform Deployment Userguide](./platform.html) for more information on how to deploy your application to the [Silverback Platform](https://silverback.apeworx.io).
