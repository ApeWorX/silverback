import subprocess
from pathlib import Path

import click
import tomlkit
import yaml

IMAGES_FOLDER_NAME = ".silverback-images"


def _has_pinned_plugin_versions(plugins) -> bool:
    if not plugins:
        return False

    return any(hasattr(plugin, "get") and plugin.get("version") is not None for plugin in plugins)


def _project_uses_pinned_ape_plugins(
    ape_config_path: Path | None = None, pyproject_path: Path | None = None
) -> bool:
    ape_config_path = ape_config_path or (Path.cwd() / "ape-config.yaml")
    pyproject_path = pyproject_path or (Path.cwd() / "pyproject.toml")

    if ape_config_path.exists():
        ape_config = yaml.safe_load(ape_config_path.read_text()) or {}
        if _has_pinned_plugin_versions(ape_config.get("plugins")):
            return True

    if pyproject_path.exists():
        pyproject = tomlkit.loads(pyproject_path.read_text())
        ape_config = pyproject.get("tool", {}).get("ape", {})
        if _has_pinned_plugin_versions(ape_config.get("plugins")):
            return True

    return False


def containerfile_template(
    bot_path: Path,
    sdk_version: str = "stable",
    requirements_txt_fname: str | None = None,
    has_pyproject_toml: bool = False,
    has_ape_config_yaml: bool = False,
    upgrade_ape_plugins: bool = True,
    contracts_folder: str | None = None,
):
    steps = [
        f"FROM ghcr.io/apeworx/silverback:{sdk_version}",
    ]

    if sdk_version == "v0.7.36":
        # NOTE: Some versions of the base image (using Ape v0.8.{46,47}) do not ensure this
        #       see: https://github.com/ApeWorX/ape/pull/2753
        # TODO: Remove in Silverback v0.8.x (no longer maintaining v0.7.36)
        steps.extend(
            [
                "USER root",
                "RUN chown harambe:harambe .",
                "USER harambe",
            ]
        )

    if requirements_txt_fname:
        steps.append(f"COPY --chown=harambe:harambe {requirements_txt_fname} requirements.txt")

    if has_pyproject_toml:
        steps.append("COPY --chown=harambe:harambe pyproject.toml .")

    if has_ape_config_yaml:
        steps.append("COPY --chown=harambe:harambe ape-config.yaml .")

    if requirements_txt_fname or has_pyproject_toml:
        steps.append("COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/")

        # NOTE: Only install project via `pyproject.toml` if `requirements-bot.txt` DNE
        install_arg = "-r requirements.txt" if requirements_txt_fname else "."
        steps.append(f"RUN uv pip install {install_arg}")

    if has_pyproject_toml or has_ape_config_yaml:
        ape_plugins_install_cmd = "RUN ape plugins install"
        if upgrade_ape_plugins:
            ape_plugins_install_cmd = f"{ape_plugins_install_cmd} -U"

        steps.append(f"{ape_plugins_install_cmd} .")

    if contracts_folder:
        steps.append(f"COPY --chown=harambe:harambe {contracts_folder} {contracts_folder}")
        steps.append("RUN ape compile")

    bot_dest = "bot/" if bot_path.is_dir() else "bot.py"
    steps.append(f"COPY --chown=harambe:harambe {bot_path} {bot_dest}")

    return "\n".join(steps)


def generate_containerfiles(path: Path, sdk_version: str = "stable"):
    (Path.cwd() / IMAGES_FOLDER_NAME).mkdir(exist_ok=True)
    uses_pinned_ape_plugins = _project_uses_pinned_ape_plugins()

    contracts_folder: str = "contracts"
    if has_ape_config_yaml := (ape_config_path := Path.cwd() / "ape-config.yaml").exists():
        contracts_folder = (
            yaml.safe_load(ape_config_path.read_text())
            .get("compiler", {})
            # NOTE: Should fall through to this last `.get` and use initial default if config DNE
            .get("contracts_folder", contracts_folder)
        )

    if has_pyproject_toml := (pyproject_path := Path.cwd() / "pyproject.toml").exists():
        contracts_folder = (
            tomlkit.loads(pyproject_path.read_text())
            .get("tool", {})
            .get("ape", {})
            .get("compiler", {})
            # NOTE: Should fall through to this last `.get` and use initial default if config DNE
            .get("contracts_folder", contracts_folder)
        )

    if not (
        # NOTE: Use this first so we can avoid using legitimate `requirements.txt`
        (Path.cwd() / (requirements_txt_fname := "requirements-bot.txt")).exists()
        or (Path.cwd() / (requirements_txt_fname := "requirements.txt")).exists()
    ):
        # NOTE: Doesn't exist so make it not be `requirements.txt`
        requirements_txt_fname = None

    if path.is_dir() and path.name == "bots":
        for bot in path.glob("*.py"):
            (Path.cwd() / IMAGES_FOLDER_NAME / f"Dockerfile.{bot.stem}").write_text(
                containerfile_template(
                    bot.relative_to(Path.cwd()),
                    sdk_version=sdk_version,
                    requirements_txt_fname=requirements_txt_fname,
                    has_pyproject_toml=has_pyproject_toml,
                    has_ape_config_yaml=has_ape_config_yaml,
                    upgrade_ape_plugins=not uses_pinned_ape_plugins,
                    contracts_folder=(
                        contracts_folder if (Path.cwd() / contracts_folder).exists() else None
                    ),
                )
            )

    else:
        (Path.cwd() / IMAGES_FOLDER_NAME / "Dockerfile.bot").write_text(
            containerfile_template(
                path.relative_to(Path.cwd()),
                sdk_version=sdk_version,
                requirements_txt_fname=requirements_txt_fname,
                has_pyproject_toml=has_pyproject_toml,
                has_ape_config_yaml=has_ape_config_yaml,
                upgrade_ape_plugins=not uses_pinned_ape_plugins,
                contracts_folder=(
                    contracts_folder if (Path.cwd() / contracts_folder).exists() else None
                ),
            )
        )


def build_container_images(
    use_docker: bool = False,
    tag_base: str | None = None,
    version: str = "latest",
    push: bool = False,
):
    if (
        not use_docker
        and (result := subprocess.run(["podman", "--version"], capture_output=True)).returncode == 0
    ):
        click.echo(f"Using {result.stdout.decode()}")
        builder_name = "podman"

    elif (result := subprocess.run(["docker", "--version"], capture_output=True)).returncode == 0:
        click.echo(f"Using {result.stdout.decode()}")
        builder_name = "docker"

    else:
        raise RuntimeError("`podman` or `docker` not detected, cannot build.")

    built_tags = []
    build_root = Path.cwd()
    for containerfile in (build_root / IMAGES_FOLDER_NAME).glob("Dockerfile.*"):
        bot_name = containerfile.suffix.lstrip(".") or "bot"
        tag = (
            f"{tag_base.lower()}-{bot_name.lower()}:{version}"
            if tag_base is not None
            else f"{build_root.name.lower()}-{bot_name.lower()}:{version}"
        )
        command = [
            builder_name,
            "build",
            "-f",
            str(containerfile.relative_to(build_root)),
            "-t",
            tag,
            ".",
        ]

        click.secho(" ".join(command), fg="green")
        try:
            subprocess.run(command, check=True)
        except subprocess.CalledProcessError as e:
            raise click.ClickException(str(e))

        built_tags.append(tag)

    if push:
        for tag in built_tags:
            subprocess.run([builder_name, "push", tag])
