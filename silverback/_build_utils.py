import subprocess
from pathlib import Path

import click

IMAGES_FOLDER_NAME = ".silverback-images"


def dockerfile_template(
    bot_path: Path,
    sdk_version: str = "stable",
    include_bot_dir: bool = False,
    has_requirements_txt: bool = False,
    has_pyproject_toml: bool = False,
    has_ape_config_yaml: bool = False,
):
    dockerfile = [
        f"FROM ghcr.io/apeworx/silverback:{sdk_version}",
        "USER root",
        "WORKDIR /app",
        "RUN chown harambe:harambe /app",
        "USER harambe",
    ]

    if has_requirements_txt:
        dockerfile.append("COPY requirements.txt .")
        dockerfile.append("RUN pip install --upgrade pip && pip install -r requirements.txt")

    # TODO: Figure out how to avoid build issues w/ pip
    # if has_pyproject_toml:
    #     dockerfile.append("COPY pyproject.toml /app")
    #     dockerfile.append("RUN pip install --upgrade pip && pip install .")

    if has_ape_config_yaml:
        dockerfile.append("COPY ape-config.yaml /app")
        dockerfile.append("RUN ape plugins install -U .")

    bot_src = f"{bot_path.parent}/{bot_path.name}" if include_bot_dir else bot_path.name
    bot_dst = "/app/bot" if bot_path.is_dir() else "/app/bot.py"
    dockerfile.append(f"COPY {bot_src} {bot_dst}")

    return "\n".join(dockerfile)


def generate_dockerfiles(path: Path, sdk_version: str = "stable"):
    (Path.cwd() / IMAGES_FOLDER_NAME).mkdir(exist_ok=True)

    build_options = dict(
        sdk_version=sdk_version,
        has_requirements_txt=(Path.cwd() / "requirements.txt").exists(),
        has_pyproject_toml=(Path.cwd() / "pyproject.toml").exists(),
        has_ape_config_yaml=(Path.cwd() / "ape-config.yaml").exists(),
    )

    if path.is_dir() and path.name == "bots":
        for bot in path.glob("*.py"):
            bot = bot.relative_to(Path.cwd())
            (Path.cwd() / IMAGES_FOLDER_NAME / f"Dockerfile.{bot.stem}").write_text(
                dockerfile_template(bot, include_bot_dir=True, **build_options)
            )

    else:
        (Path.cwd() / IMAGES_FOLDER_NAME / "Dockerfile.bot").write_text(
            dockerfile_template(path, **build_options)
        )


def build_docker_images():
    for dockerfile in (Path.cwd() / IMAGES_FOLDER_NAME).glob("Dockerfile.*"):
        command = f"docker build -f {dockerfile.relative_to(Path.cwd())} ."
        click.secho(f"{command}", fg="green")

        try:
            subprocess.run(command, shell=True, check=True)
        except subprocess.CalledProcessError as e:
            raise click.ClickException(str(e))
