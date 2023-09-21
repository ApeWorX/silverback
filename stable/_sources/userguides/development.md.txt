# Developing a Silverback Application

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

If you have heavier resources you want to load during startup, or otherwise perform some data collection prior to starting the bot, you can add a startup function like so:

```py
@app.on_startup()
def handle_on_worker_startup(state):
    ...
```

This function comes a parameter `state` that you can use for storing the results of your startup computation or resources that you have provisioned.
It's import to note that this is useful for ensuring that your workers (of which there can be multiple) have the resources necessary to properly handle any updates you want to make in your handler functions, such as connecting to the Telegram API, an SQL or NoSQL database connection, or something else.
The `state` variable is also useful as this gets made available to each handler method so other stateful quantities can be maintained for other uses.

TODO: Add more information about `state`

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

```note
If you configure your application to use a signer, and that signer signs anything given to it, remember that you can lose substational amounts of funds if you deploy this to a production network.
Always test your applications throughly before deploying.
```

## Testing your Application

TODO: Add backtesting mode w/ `silverback test`

## Deploying to the Silverback Platform

TODO: Add packaging and deployment to the Silverback platform, once available.
