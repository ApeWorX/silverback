# Quick Start

Silverback lets you create and deploy your own Python bots that respond to on-chain events.
The Silverback library leverages the [Ape](https://docs.apeworx.io/ape/stable/userguides/quickstart) development framework as well as it's ecosystem of plugins and packages to enable you to develop simple-yet-sophisticated automated applications that can listen and respond to live chain data.

Silverback applications are excellent for use cases that involve continuously monitoring and responding to on-chain events, such as newly confirmed blocks or contract event logs.

Some examples of these types of applications:

- Monitoring new pool creations, and depositing liquidity
- Measuring trading activity of popular pools
- Listening for large swaps to update a telegram group

## Documentation

Read the [development userguide](https://docs.apeworx.io/silverback/stable/userguides/development.html) to learn more how to develop an application.

## Dependencies

- [python3](https://www.python.org/downloads) version 3.10 or greater, python3-dev

## Installation

Silverback relies heavily on the Ape development framework, so it's worth it to familarize yourself with how to install Ape and it's plugins using the [Ape installation userguide](https://docs.apeworx.io/ape/latest/userguides/quickstart#installation).

### via `pip`

You can install the latest release via [`pip`](https://pypi.org/project/pip/):

```bash
pip install silverback
```

### via `setuptools`

You can clone the repository and use [`setuptools`](https://github.com/pypa/setuptools) for the most up-to-date version:

```bash
git clone https://github.com/ApeWorX/silverback.git silverback
cd silverback
python3 setup.py install
```

## Quick Usage

Checkout [the example](./example.py) to see how to use the library.

To run your bot against a live network, this SDK includes a simple runner you can use via:

```sh
$ silverback run "example:app" --network :mainnet:alchemy
```

**NOTE**: The example is designed to work with Python 3.10+, and we suggest using 3.11+ for speed.

## Docker Usage

```sh
$ docker run --volume $PWD:/home/harambe/project --volume ~/.tokenlists:/home/harambe/.tokenlists apeworx/silverback:latest run "example:app" --network :mainnet:alchemy
```

**NOTE**: The Docker image we publish uses Python 3.11

## Development

This project is in development and should be considered a beta.
Things might not be in their final state and breaking changes may occur.
Comments, questions, criticisms and pull requests are welcomed.

## Full Environment Execution Example

Running the `Quick Usage` and `Docker Usage` examples will fail if you do not have a full environment setup.

First, it is suggested that you use a virtual environment, and then install the Silverback application. Choose any of your liking. This is not a requirement, but will help with setup if you do not have a `~/.tokenslists` folder.

```bash
python3 -m venv venv
source ./venv/bin/activate
pip install .
```

Next, you will need a Web3 Alchemy key. If you attempt to run the `Docker Usage` command, you will get the following error:

```bash
$ docker run --volume $PWD:/home/harambe/project --volume ~/.tokenlists:/home/harambe/.tokenlists apeworx/silverback:latest run "example:app" --network :mainnet:alchemy
Traceback (most recent call last):
  File "/usr/local/bin/silverback", line 8, in <module>
    sys.exit(cli())
  File "/usr/local/lib/python3.10/site-packages/click/core.py", line 1157, in __call__
    return self.main(*args, **kwargs)
  File "/usr/local/lib/python3.10/site-packages/click/core.py", line 1078, in main
    rv = self.invoke(ctx)
  File "/usr/local/lib/python3.10/site-packages/click/core.py", line 1688, in invoke
    return _process_result(sub_ctx.command.invoke(sub_ctx))
  File "/usr/local/lib/python3.10/site-packages/ape/cli/commands.py", line 95, in invoke
    with network_ctx as provider:
  File "/usr/local/lib/python3.10/site-packages/ape/api/networks.py", line 679, in __enter__
    return self.push_provider()
  File "/usr/local/lib/python3.10/site-packages/ape/api/networks.py", line 692, in push_provider
    self._provider.connect()
  File "/usr/local/lib/python3.10/site-packages/ape_ethereum/provider.py", line 113, in connect_wrapper
    connect(self)
  File "/usr/local/lib/python3.10/site-packages/ape_alchemy/provider.py", line 102, in connect
    self._web3 = Web3(HTTPProvider(self.uri))
  File "/usr/local/lib/python3.10/site-packages/ape_alchemy/provider.py", line 72, in uri
    raise MissingProjectKeyError(options)
ape_alchemy.exceptions.MissingProjectKeyError: Must set one of $WEB3_ALCHEMY_PROJECT_ID, $WEB3_ALCHEMY_API_KEY, $WEB3_ETHEREUM_MAINNET_ALCHEMY_PROJECT_ID, $WEB3_ETHEREUM_MAINNET_ALCHEMY_API_KEY.
```

Go to [Alchemy](https://alchemy.com), create an account, then create an application in their dashboard, and copy the API Key.

Another requirement for the command from `Docker Usage` to run, is to have a `~/.tokenslists` hidden folder in your home folder. We mount that folder into the docker container, see below:

```bash
... --volume ~/.tokenlists:/home/harambe/.tokenlists ...
```

It is suggested to install the [ape-tokens](https://github.com/ApeWorX/ape-tokens) plugin

```bash
ape plugins install ape-tokens
```

Then use the CLI to install a token list. From the [ape-tokens](https://github.com/ApeWorX/ape-tokens?tab=readme-ov-file#quick-usage) README, it is suggested that you run the command:

```bash
ape tokens install tokens.1inch.eth
```

Check that the list of tokens exist, and that the `~/.tokenlists` folder exists:

```bash
$ ape tokens list-tokens
...
0x0bc529c00C6401aEF6D220BE8C6Ea1667F6Ad93e (YFI)
...
0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48 (USDC)
...
```

```bash
$ ls -la ~/ | grep .tokenlists
drwxrwxr-x  2 usr grp         4096 Apr 11 09:26 .tokenlists
```

You can comment out the two comments that manage the `ContractLog` if you do not have an established contract.

```python
...
# @app.on_(USDC.Transfer, start_block=18588777, new_block_timeout=25)
# # NOTE: Typing isn't required
# def exec_event1(log):
#     if log.log_index % 7 == 3:
#         # If you ever want the app to shutdown under some scenario, call this exception
#         raise CircuitBreaker("Oopsie!")
#     return {"amount": log.amount}
#
#
# @app.on_(YFI.Approval)
# # Any handler function can be async too
# async def exec_event2(log: ContractLog):
#     return log.amount
...
```

Then run the following command:

```bash
$ docker run -e WEB3_ALCHEMY_API_KEY='your-alchemy-api-key-here' --volume $PWD:/home/harambe/project --volume ~/.tokenlists:/home/harambe/.tokenlists apeworx/silverback:latest run "example:app" --network :mainnet:alchemy
```
