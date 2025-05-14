from pathlib import Path

import pytest

from silverback._build_utils import dockerfile_template
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
        dict(include_bot_dir=True),
    ],
)
def test_dockerfile_generation(build_args):
    dockerfile = dockerfile_template(
        Path(__file__).parent.parent / "bots" / "example.py", **build_args
    )
    assert "example.py" in dockerfile
    assert build_args.get("sdk_version", "stable") in dockerfile
    if requirements_txt_fname := build_args.get("requirements_txt_fname"):
        assert requirements_txt_fname in dockerfile
    if build_args.get("has_pyproject_toml"):
        assert "pyproject.toml" in dockerfile
    if build_args.get("has_ape_config_yaml"):
        assert "ape-config.yaml" in dockerfile
    if contracts_folder := build_args.get("contracts_folder"):
        assert contracts_folder in dockerfile
    if build_args.get("include_bot_dir"):
        assert "bots/" in dockerfile
