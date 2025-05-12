import subprocess
from pathlib import Path

import click
import yaml

IMAGES_FOLDER_NAME = ".silverback-images"


def dockerfile_template(
    bot_path: Path,
    sdk_version: str = "stable",
    include_bot_dir: bool = False,
    has_requirements_txt: bool = False,
    has_pyproject_toml: bool = False,
    has_ape_config_yaml: bool = False,
    contracts_folder: str | None = None,
):
    dockerfile = [
        f"FROM ghcr.io/apeworx/silverback:{sdk_version}",
        "USER root",
        "WORKDIR /app",
        "RUN chown harambe:harambe /app",
        "USER harambe",
    ]

    if has_requirements_txt or has_pyproject_toml:
        dockerfile.append("RUN pip install --upgrade pip")

    if has_requirements_txt:
        dockerfile.append("COPY requirements.txt .")
        dockerfile.append("RUN pip install -r requirements.txt")

    if has_pyproject_toml:
        dockerfile.append("COPY pyproject.toml .")
        dockerfile.append("RUN  pip install .")

    if has_ape_config_yaml:
        dockerfile.append("COPY ape-config.yaml .")

    if has_pyproject_toml or has_ape_config_yaml:
        dockerfile.append("RUN ape plugins install -U .")

    if contracts_folder:
        dockerfile.append(f"COPY {contracts_folder} /app")
        dockerfile.append("RUN ape compile")

    bot_src = f"{bot_path.parent}/{bot_path.name}" if include_bot_dir else bot_path.name
    bot_dst = "/app/bot" if bot_path.is_dir() else "/app/bot.py"
    dockerfile.append(f"COPY {bot_src} {bot_dst}")

    return "\n".join(dockerfile)


def generate_dockerfiles(path: Path, sdk_version: str = "stable"):
    (Path.cwd() / IMAGES_FOLDER_NAME).mkdir(exist_ok=True)

    contracts_folder: str | None = "contracts"
    if has_ape_config_yaml := (ape_config_path := Path.cwd() / "ape-config.yaml").exists():
        contracts_folder = (
            yaml.safe_load(ape_config_path.read_text())
            .get("compiler", {})
            .get("contracts_folder", contracts_folder)
        )

    assert contracts_folder  # make mypy happy
    if not ((Path.cwd() / contracts_folder)).exists():
        contracts_folder = None

    if path.is_dir() and path.name == "bots":
        for bot in path.glob("*.py"):
            bot = bot.relative_to(Path.cwd())
            (Path.cwd() / IMAGES_FOLDER_NAME / f"Dockerfile.{bot.stem}").write_text(
                dockerfile_template(
                    bot,
                    include_bot_dir=True,
                    sdk_version=sdk_version,
                    has_requirements_txt=(Path.cwd() / "requirements.txt").exists(),
                    has_pyproject_toml=(Path.cwd() / "pyproject.toml").exists(),
                    has_ape_config_yaml=has_ape_config_yaml,
                    contracts_folder=contracts_folder,
                )
            )

    else:
        (Path.cwd() / IMAGES_FOLDER_NAME / "Dockerfile.bot").write_text(
            dockerfile_template(
                path,
                sdk_version=sdk_version,
                has_requirements_txt=(Path.cwd() / "requirements.txt").exists(),
                has_pyproject_toml=(Path.cwd() / "pyproject.toml").exists(),
                has_ape_config_yaml=has_ape_config_yaml,
                contracts_folder=contracts_folder,
            )
        )


def build_docker_images():
    for dockerfile in (Path.cwd() / IMAGES_FOLDER_NAME).glob("Dockerfile.*"):
        command = f"docker build -f {dockerfile.relative_to(Path.cwd())} ."
        click.secho(f"{command}", fg="green")

        try:
            subprocess.run(command, shell=True, check=True)
        except subprocess.CalledProcessError as e:
            raise click.ClickException(str(e))
