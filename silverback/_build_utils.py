import shlex
import subprocess
from functools import singledispatchmethod
from pathlib import Path
from typing import Union

import click
from ape.utils.os import clean_path

DOCKERFILE_CONTENT = """
FROM ghcr.io/apeworx/silverback:stable
USER root
WORKDIR /app
RUN chown harambe:harambe /app
USER harambe
"""


# Note: Python3.12 supports subclassing pathlib.Path
class BasePath(Path):
    _flavour = type(Path())._flavour  # type: ignore


class FilePath(BasePath):
    """A subclass of Path representing a file."""


class DirPath(BasePath):
    """A subclass of Path representing a path"""


def get_path(path: Path):
    if path.is_file():
        return FilePath(str(path))
    elif path.is_dir():
        return DirPath(str(path))
    else:
        raise ValueError(f"{path} is neither a file nor a directory")


PathType = Union["FilePath", "DirPath"]


def generate_dockerfiles(path: Path):
    path = get_path(path)
    dg = DockerfileGenerator()
    dg.generate_dockerfiles(path)


def build_docker_images(path: Path):
    DockerfileGenerator.build_images(path)


class DockerfileGenerator:

    @property
    def dockerfile_name(self):
        return self._dockerfile_name

    @dockerfile_name.setter
    def dockerfile_name(self, name):
        self._dockerfile_name = name

    @singledispatchmethod
    def generate_dockerfiles(self, path: PathType):
        """
        Will generate a file based on path type
        """
        raise NotImplementedError(f"Path type {type(path)} not supported")

    @generate_dockerfiles.register
    def _(self, path: FilePath):
        dockerfile_content = self._check_for_requirements(DOCKERFILE_CONTENT)
        self.dockerfile_name = f"Dockerfile.{path.parent.name}-bot"
        dockerfile_content += f"COPY {path.name}/ /app/bot.py\n"
        self._build_helper(dockerfile_content)

    @generate_dockerfiles.register
    def _(self, path: DirPath):
        bots = self._get_all_bot_files(path)
        for bot in bots:
            dockerfile_content = self._check_for_requirements(DOCKERFILE_CONTENT)
            if bot.name == "__init__.py" or bot.name == "bot.py":
                self.dockerfile_name = f"Dockerfile.{bot.parent.parent.name}-bot"
                dockerfile_content += f"COPY {path.name}/ /app/bot\n"
            else:
                self.dockerfile_name = f"Dockerfile.{bot.name.replace('.py', '')}"
                dockerfile_content += f"COPY {path.name}/{bot.name} /app/bot.py\n"
            self._build_helper(dockerfile_content)

    def _build_helper(self, dockerfile_c: str):
        """
        Used in multiple places in build.
        """
        dockerfile_path = Path.cwd() / ".silverback-images" / self.dockerfile_name
        dockerfile_path.parent.mkdir(exist_ok=True)
        dockerfile_path.write_text(dockerfile_c.strip() + "\n")
        click.echo(f"Generated {clean_path(dockerfile_path)}")

    def _check_for_requirements(self, dockerfile_content):
        if (Path.cwd() / "requirements.txt").exists():
            dockerfile_content += "COPY requirements.txt .\n"
            dockerfile_content += (
                "RUN pip install --upgrade pip && pip install -r requirements.txt\n"
            )

        if (Path.cwd() / "ape-config.yaml").exists():
            dockerfile_content += "COPY ape-config.yaml .\n"
            dockerfile_content += "RUN ape plugins install -U .\n"

        return dockerfile_content

    def _get_all_bot_files(self, path: DirPath):
        files = sorted({file for file in path.iterdir() if file.is_file()}, reverse=True)
        bots = []
        for file in files:
            if file.name == "__init__.py" or file.name == "bot.py":
                bots = [file]
                break
            bots.append(file)
        return bots

    @staticmethod
    def build_images(path: Path):
        dockerfiles = {file for file in path.iterdir() if file.is_file()}
        for file in dockerfiles:
            try:
                command = shlex.split(
                    "docker build -f "
                    f"./{file.parent.name}/{file.name} "
                    f"-t {file.name.split('.')[1]}:latest ."
                )
                result = subprocess.run(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    check=True,
                )
                click.echo(result.stdout)
            except subprocess.CalledProcessError as e:
                click.echo("Error during docker build:")
                click.echo(e.stderr)
                raise
