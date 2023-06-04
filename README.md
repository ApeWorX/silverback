# Quick Start

Ape SDK for the Silverback platform

## Dependencies

- [python3](https://www.python.org/downloads) version 3.8 or greater, python3-dev

## Installation

### via `pip`

You can install the latest release via [`pip`](https://pypi.org/project/pip/):

```bash
pip install silverback
```

### via `setuptools`

You can clone the repository and use [`setuptools`](https://github.com/pypa/setuptools) for the most up-to-date version:

```bash
git clone https://github.com/SilverBackLtd/sdk.git silverback
cd silverback
python3 setup.py install
```

## Quick Usage

Checkout [the example](./example.py) to see how to use the library.

To run your bot against a live network, this SDK includes a simple runner you can use via:

```sh
$ silverback run "example:app" --network :mainnet:alchemy
```

## Development

This project is in development and should be considered a beta.
Things might not be in their final state and breaking changes may occur.
Comments, questions, criticisms and pull requests are welcomed.
