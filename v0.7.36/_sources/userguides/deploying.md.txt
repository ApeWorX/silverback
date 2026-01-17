# Deploying Bots

In this guide, we are going to show you more details on how to build and deploy your bots
to a production hosting environment, like the [Silverback Platform](https://silverback.apeworx.io).

```{important}
You need a containerization tool to get started, like [Docker](https://docs.docker.com)
or [Podman](https://podman-desktop.io) (ships on some Linux distros).
```

```{note}
If both tools are installed, Silverback prefers using Podman over Docker.
Podman is daemonless and allows rootless container execution, enhancing system security.
```

## Building your Bot

In order to deploy your bot to a "production" orchestration solution,
we will first have to containerize them and then publish to a container registry.
Building a container for your bot can be an advanced topic,
so we have included the [`silverback build`][silverback-build] subcommand to help assist in generating successful builds.

To auto-generate Dockerfiles for your bots before building them, from the root of your project you can run:

```bash
silverback build --generate
```

This will generate container build files in a special directory in the root of your project.

As an example, if you have a bots directory that looks like:

```
bots/
├── botA.py
├── botB.py
└── botC.py
```

This method will generate 3 Dockerfiles:

```
.silverback-images/
├── Dockerfile.botA
├── Dockerfile.botB
└── Dockerfile.botC
```

It will then automatically build them for you (monitor the output for any issues).
Assuming the build commands succeed, you will have fully-built images that you can push into a Registry.

```{note}
If you chose the `bot/` project structure and `bot` is a Python package, you will be unable to use the dockerfile generation feature.
This method will warn you that you are generating bots for a python package, but will not stop you from attempting to do so.
When you generate the dockerfiles, be aware that it will only copy the `bot/` folder into the Dockerfile,
and will not include any supporting python functionality.
Each python module is expected to run independently.
If you require more complex bots, you should use custom dockerfiles.
```

You can also re-build your images now using the following (assuming you don't modify the structure of your project):

```bash
silverback build
```

This can be useful if you make small logic changes to your bot.

## Publishing your Bot

Use the `docker push` (or `podman push`) command to push to a Registry so you can use it in cloud-based deployments.

```bash
docker push your-registry-url/project/botA:latest
```

### Using the Github Action

The ApeWorX team has created a [Github Action](https://github.com/SilverbackLtd/build-action)
for building and publishing your bot images in a bot project repository repo using Github Actions CI.

If you are unfamiliar with docker and container registries, you should use the Action.

You do not need to build locally using `silverback build` if you use the Github Action to publish,
but it is there to help you if you are having problems figuring out how to build in CI,
or if your bot images will not run in production successfully.

You can also debug containers in an environment similar to the Silverback Platform using
[Distributed Execution][distributed-execution] Mode.

[distributed-execution]: ./advanced.html#distributed-execution
[silverback-build]: ../commands/run.html#silverback-build
