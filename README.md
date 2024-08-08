# Quick Start

Silverback lets you create and deploy your own Python bots that respond to on-chain events.
The Silverback library leverages the [Ape](https://docs.apeworx.io/ape/stable/userguides/quickstart) development framework as well as it's ecosystem of plugins and packages to enable you to develop simple-yet-sophisticated automated applications that can listen and respond to live chain data.

Silverback applications are excellent for use cases that involve continuously monitoring and responding to on-chain events, such as newly confirmed blocks or contract event logs.

Some examples of these types of applications:

- Monitoring new pool creations, and depositing liquidity
- Measuring trading activity of popular pools
- Listening for large swaps to update a telegram group

## Documentation

Please read the [development userguide](https://docs.apeworx.io/silverback/stable/userguides/development.html) for more information on how to develop an application.

## Dependencies

- [python3](https://www.python.org/downloads) version 3.10 or greater, python3-dev

## Installation

Silverback relies heavily on the Ape development framework, so it's worth it to familarize yourself with how to install Ape and it's plugins using the [Ape installation userguide](https://docs.apeworx.io/ape/latest/userguides/quickstart#installation).

```{note}
It is suggested that you use a virtual environment of your choosing, and then install the Silverback package via one of the following options.
```

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

Checkout [the example](https://github.com/ApeWorX/silverback/blob/main/example.py) to see how to use the library.

```{note}
The example makes use of the [Ape Tokens](https://github.com/ApeWorX/ape-tokens) plugin.
Be sure to properly configure your environment for the USDC and YFI tokens on Ethereum mainnet.
```

To run your bot against a live network, this SDK includes a simple runner command you can use via:

```sh
$ silverback run "example:app" --network :mainnet:alchemy
```

```{note}
This runner uses an in-memory task broker by default.
If you want to learn more about what that means, please visit the [development userguide](https://docs.apeworx.io/silverback/stable/userguides/development.html).
```

## Docker Usage

```sh
$ docker run --volume $PWD:/home/harambe/project --volume ~/.tokenlists:/home/harambe/.tokenlists apeworx/silverback:latest run "example:app" --network :mainnet
```

```{note}
The Docker image we publish uses Python 3.11.
```

## Setting Up Your Environment

Running the `Quick Usage` and `Docker Usage` with the provided example will fail if you do not have a fully-configured environment.
Most common issues when using the SDK stem from the proper configuration of Ape plugins to unlock the behavior you desire.

You should use a provider that supports websockets to run silverback.
If you want to use a hosted provider with websocket support like Alchemy to run this example, you will need a Alchemy API key for Ethereum mainnet.
If you attempt to run the `Docker Usage` command without supplying this key, you will get the following error:

```bash
$ docker run --volume $PWD:/home/harambe/project --volume ~/.tokenlists:/home/harambe/.tokenlists apeworx/silverback:latest run "example:app" --network :mainnet:alchemy
Traceback (most recent call last):
  ...
ape_alchemy.exceptions.MissingProjectKeyError: Must set one of $WEB3_ALCHEMY_PROJECT_ID, $WEB3_ALCHEMY_API_KEY, $WEB3_ETHEREUM_MAINNET_ALCHEMY_PROJECT_ID, $WEB3_ETHEREUM_MAINNET_ALCHEMY_API_KEY.
```

Go to [Alchemy](https://alchemy.com), create an account, then create an application in their dashboard, and copy the API Key.

Another requirement for the command from `Docker Usage` to run the given example is that it uses [ape-tokens](https://github.com/ApeWorX/ape-tokens) plugin to look up token interfaces by symbol.
In order for this to work, you should have installed and configured that plugin using a token list that includes both YFI and USDC on Ethereum mainnet.
Doing this will give you a `~/.tokenlists` hidden folder in your home folder that you must mount into the docker container with the following flag:

```bash
... --volume ~/.tokenlists:/home/harambe/.tokenlists ...
```

```{note}
It is suggested to install the 1inch tokenlist via `ape tokens install tokens.1inch.eth`.
See the [ape-tokens](https://github.com/ApeWorX/ape-tokens?tab=readme-ov-file#quick-usage) README for more information.
```

To check that both of the tokens exist in your configured tokenlist, you can execute this command:

```bash
$ ape tokens token-info YFI
      Symbol: YFI
        Name: yearn.finance
    Chain ID: 1
     Address: 0x0bc529c00C6401aEF6D220BE8C6Ea1667F6Ad93e
    Decimals: 18

$ ape tokens token-info USDC
      Symbol: USDC
        Name: Circle USD
    Chain ID: 1
     Address: 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48
    Decimals: 6
```

```{note}
If you want, you can comment out the two functions `exec_event1` and `exec_event2` that handle the contract log events from these contracts if you do not have the configured tokenlist, then your command should work.
```

## Development

This project is under active development in preparation of the release of the [Silverback Platform](https://silverback.apeworx.io).
Things might not be in their final state and breaking changes may occur.
Comments, questions, criticisms and pull requests are welcomed.

See [Contributing](https://github.com/ApeWorX/silverback/blob/main/CONTRIBUTING.md) for more information.
