from pathlib import Path

import pytest

from silverback._build_utils import (
    IMAGES_FOLDER_NAME,
    containerfile_template,
    generate_containerfiles,
)
from silverback.utils import decode_topics_from_string, encode_topics_to_string


@pytest.mark.parametrize(
    "topics",
    [
        [],
        ["0x1"],
        [None, "0x2"],
        ["0x1", "0x2"],
        [["0x1", "0x2"], ["0x1", "0x2"]],
    ],
)
def test_topic_encoding(topics):
    assert decode_topics_from_string(encode_topics_to_string(topics)) == topics


EXAMPLE_BOT_PATH = Path(__file__).parent.parent / "bots" / "example.py"


@pytest.fixture(scope="module", params=["file", "folder"])
def bot_path(request):
    if request.param == "file":
        yield EXAMPLE_BOT_PATH

    else:  # Make `bot/` as a module and copy example over to it
        folder = Path(__file__).parent.parent / "bot"
        folder.mkdir(exist_ok=True)
        (folder / "__init__.py").write_text(EXAMPLE_BOT_PATH.read_text())

        try:
            yield folder

        finally:
            (folder / "__init__.py").unlink(missing_ok=True)
            folder.rmdir()


@pytest.mark.parametrize(
    "build_args",
    [
        dict(),
        dict(sdk_version="latest"),
        dict(requirements_txt_fname="requirements.txt"),
        dict(requirements_txt_fname="requirements-bot.txt"),
        dict(has_pyproject_toml=True),
        dict(has_ape_config_yaml=True),
        dict(contracts_folder="src"),
    ],
)
def test_containerfile_generation(bot_path, build_args):
    containerfile = containerfile_template(bot_path, **build_args)

    assert bot_path.name in containerfile
    assert build_args.get("sdk_version", "stable") in containerfile
    if requirements_txt_fname := build_args.get("requirements_txt_fname"):
        assert requirements_txt_fname in containerfile
    if build_args.get("has_pyproject_toml"):
        assert "pyproject.toml" in containerfile
    if build_args.get("has_ape_config_yaml"):
        assert "ape-config.yaml" in containerfile
    if contracts_folder := build_args.get("contracts_folder"):
        assert contracts_folder in containerfile


@pytest.mark.parametrize(
    ("config_name", "config_contents"),
    [
        (
            "ape-config.yaml",
            'plugins:\n  - name: "aws"\n    version: ">=0.8.1b1"\n',
        ),
        (
            "pyproject.toml",
            '[[tool.ape.plugins]]\nname = "aws"\nversion = ">=0.8.1b1"\n',
        ),
    ],
)
def test_generate_containerfiles_avoids_upgrade_for_pinned_plugins(
    tmp_path, monkeypatch, config_name, config_contents
):
    bot_path = tmp_path / "bot.py"
    bot_path.write_text("from silverback import SilverbackBot\n")
    (tmp_path / config_name).write_text(config_contents)

    monkeypatch.chdir(tmp_path)
    generate_containerfiles(bot_path)

    containerfile = (tmp_path / IMAGES_FOLDER_NAME / "Dockerfile.bot").read_text()
    assert "RUN ape plugins install ." in containerfile
    assert "RUN ape plugins install -U ." not in containerfile


@pytest.mark.parametrize(
    ("config_name", "config_contents"),
    [
        (
            "ape-config.yaml",
            'plugins:\n  - name: "aws"\n',
        ),
        (
            "pyproject.toml",
            '[[tool.ape.plugins]]\nname = "aws"\n',
        ),
    ],
)
def test_generate_containerfiles_keeps_upgrade_for_unpinned_plugins(
    tmp_path, monkeypatch, config_name, config_contents
):
    bot_path = tmp_path / "bot.py"
    bot_path.write_text("from silverback import SilverbackBot\n")
    (tmp_path / config_name).write_text(config_contents)

    monkeypatch.chdir(tmp_path)
    generate_containerfiles(bot_path)

    containerfile = (tmp_path / IMAGES_FOLDER_NAME / "Dockerfile.bot").read_text()
    assert "RUN ape plugins install -U ." in containerfile
