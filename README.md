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

- [python3](https://www.python.org/downloads) version 3.8 or greater, python3-dev

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

## Docker Usage

```sh
$ docker run --volume $PWD:/home/harambe/project --volume ~/.tokenlists:/home/harambe/.tokenlists apeworx/silverback:latest run "example:app" --network :mainnet:alchemy
```

## Development

This project is in development and should be considered a beta.
Things might not be in their final state and breaking changes may occur.
Comments, questions, criticisms and pull requests are welcomed.
