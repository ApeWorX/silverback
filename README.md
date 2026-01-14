# Overview

Silverback lets you create your own Python bots that respond in real-time to blockchain events.
The Silverback library leverages the [Ape](https://docs.apeworx.io/ape/stable/userguides/quickstart)
development framework, as well as it's ecosystem of plugins and packages, to let you to develop
capable automated bots that trigger your own custom Python logic whenever a specific event occurs.

Silverback bots are excellent for use cases that require continuous monitoring for specific conditions,
and then require a direct response such as sending a notification or submitting a transaction.

Some examples of these types of bots:

- Index raw events from a protocol, and derive bespoke metrics in real-time
- Monitor new pool creations, and deposit liquidity if conditions are favorable
- Measure trading activity of a pool, and use those metrics to inform your own trading algorithm
- Listen for large swaps by whales, in order to update a Telegram group or Discord channel
- Perform protocol actions such as liquidations or oracle updates

## Dependencies

- [python3](https://www.python.org/downloads) version 3.10 or greater, python3-dev

## Installation

Silverback relies heavily on the Ape development framework, so it's worth it to familarize yourself
with how to install Ape and any necessary plugins using the
[Ape installation userguide](https://docs.apeworx.io/ape/stable/userguides/quickstart#installation).

```{note}
It is suggested that you use a virtual environment of your choosing,
and then install the Silverback package via one of the following options.
```

### via `pip`

You can install the latest release via [`pip`](https://pypi.org/project/pip):

```bash
pip install silverback
```

### via `uv`

You can install the latest release via [`uv`](https://docs.astral.sh/uv/getting-started/installation)

```bash
uv tool install silverback
```

```{note}
To install 2nd/3rd party Ape plugins using `uv tool`, you will need to add
`--with ape-<plugin name>` to ensure the environment has them.
```

## Quick Usage

View [the example](https://github.com/ApeWorX/silverback/blob/main/bots/example.py)
to see how to use the library to build a bot.
The example shows off a variety of Silverback's features and functionality,
and you can learn more about this in the [development guide](https://docs.apeworx.io/silverback/stable/userguides/development).

### Running Locally

Download the example to `bots/example.py`.

```{note}
The example makes use of the [Ape Tokens](https://github.com/ApeWorX/ape-tokens) plugin.
Be sure to properly configure your environment for the USDC and YFI tokens on Ethereum mainnet.
```

To run this bot against a live network, this SDK includes a simple command you can use as follows:

```sh
silverback run example --network :mainnet:alchemy
```

### Running from a Container

Silverback makes it really easy to containerize your bots in order to run them inside of a container orchestrator.
This makes running our example even easier, all you need is `docker` (or `podman`) installed and then you can run via:

```sh
$ docker run -it ghcr.io/apeworx/silverback-example:latest -- run --network :mainnet
```

```{note}
The base silverback image we publish uses Python 3.11 for it's runtime.
Make sure all your packages are installable using this version.
```

For convienence, we offer the [Silverback Platform](https://silverback.apeworx.io),
which is a hosted service that allows you to run many of your bots concurrently on different blockchain networks.

## Development

This project is under active development to match the capabilities of the
[Silverback Platform](https://silverback.apeworx.io).
Things might not be in their final state and breaking changes may occur in minor revisions.
Comments, questions, criticisms and pull requests are welcomed.

See [Contributing](https://github.com/ApeWorX/silverback?tab=contributing-ov-file) for more information.
