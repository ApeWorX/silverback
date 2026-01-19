from pathlib import Path

import pytest

from silverback._build_utils import containerfile_template
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
