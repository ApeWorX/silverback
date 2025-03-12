# Quick Start

Silverback lets you create and deploy your own Python bots that respond to on-chain events.
The Silverback library leverages the [Ape](https://docs.apeworx.io/ape/stable/userguides/quickstart) development framework as well as it's ecosystem of plugins and packages to enable you to develop simple-yet-sophisticated automated bots that can listen and respond to live chain data.

Silverback bots are excellent for use cases that involve continuously monitoring and responding to on-chain events, such as newly confirmed blocks or contract event logs.

Some examples of these types of bots:

- Monitoring new pool creations, and depositing liquidity
- Measuring trading activity of popular pools
- Listening for large swaps to update a telegram group

## Documentation

Please read the [development userguide](https://docs.apeworx.io/silverback/stable/userguides/development.html) for more information on how to develop a bot.

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

Checkout [the example](https://github.com/ApeWorX/silverback/blob/main/bots/example.py) to see how to use the library.

```{note}
The example makes use of the [Ape Tokens](https://github.com/ApeWorX/ape-tokens) plugin.
Be sure to properly configure your environment for the USDC and YFI tokens on Ethereum mainnet.
```

To run your bot against a live network, this SDK includes a simple bot command you can use via:

```sh
$ silverback run example --network :mainnet:alchemy
```

```{note}
This bot uses an in-memory task broker by default.
If you want to learn more about what that means, please visit the [development userguide](https://docs.apeworx.io/silverback/stable/userguides/development.html).
```

```{note}
It is suggested that you create a bots/ folder in the root of your project.
Silverback will automatically register files in this folder as separate bots that can be run via the `silverback run` command.
```

```{note}
It is also suggested that you treat this as a scripts folder, and do not include an __init__.py
If you have a complicated project, follow the previous example to ensure you run the bot correctly.
```

```{note}
A final suggestion would be to name your `SilverbackBot` object `bot`. Silverback automatically searches 
for this object name when running. If you do not do so, once again, ensure you replace `example` with 
`example:<name-of-object>` the previous example.
```

To auto-generate Dockerfiles for your bots, from the root of your project, you can run:

```bash
silverback build --generate
```

This will place the generated dockerfiles in a special directory in the root of your project.

As an example, if you have a bots directory that looks like:

```
bots/
├── botA.py
├── botB.py
├── botC.py
```

This method will generate 3 Dockerfiles:

```
.silverback-images/
├── Dockerfile.botA
├── Dockerfile.botB
├── Dockerfile.botC
```

These Dockerfiles can be deployed with the `docker push` command documented in the next section so you can use it in cloud-based deployments.

```{note}
As an aside, if your bots/ directory is a python package, you will cause conflicts with the dockerfile generation feature. This method will warn you that you are generating bots for a python package, but will not stop you from doing so. If you choose to generate dockerfiles, the user should be aware that it will only copy each individual file into the Dockerfile, and will not include any supporting python functionality. Each python file is expected to run independently. If you require more complex bots, you will have to build a custom docker image.
```

## Docker Usage

```sh
$ docker run -it apeworx/silverback-example:latest run --network :mainnet
```

```{note}
The Docker image we publish uses Python 3.11.
```

## Development

This project is under active development in preparation of the release of the [Silverback Platform](https://silverback.apeworx.io).
Things might not be in their final state and breaking changes may occur.
Comments, questions, criticisms and pull requests are welcomed.

See [Contributing](https://github.com/ApeWorX/silverback/blob/main/CONTRIBUTING.md) for more information.
