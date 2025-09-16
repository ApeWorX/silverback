import os
from pathlib import Path
from typing import Any, Mapping


def test_run_no_bots(cli, runner):
    result = runner.invoke(cli, "run")
    assert result.exit_code != 0
    expected = (
        "Usage: cli run [OPTIONS] [BOT]\n"
        "Try 'cli run --help' for help.\n\n"
        "Error: Invalid value for '[BOT]': "
        "Nothing to run: No bot argument(s) given and no bots module found.\n"
    )
    assert result.output == expected


def test_run_verbosity(cli, runner):
    """
    A test showing the verbosity option works.
    If it didn't work, the exit code would not be 0 here.
    """
    result = runner.invoke(cli, ["run", "--help", "--verbosity", "DEBUG"], catch_exceptions=False)
    assert result.exit_code == 0


def write_env(
    tmp_path: Path,
    name: str,
    mapping: Mapping[str, Any] | None = None,
    content: str | None = None,
) -> Path:
    p = tmp_path / name
    if content is not None:
        p.write_text(content)
    elif mapping is not None:
        p.write_text("".join(f"{k}={v}\n" for k, v in mapping.items()))
    else:
        p.write_text("")
    return p


def test_env_file_single_file(cli, runner, tmp_path, monkeypatch):
    ADDRESS = "0x786f4dBD3675A59140d39b421adbE9A756836561"
    env_file = write_env(tmp_path, "test.env", mapping={"ADDRESS": ADDRESS})

    result = runner.invoke(cli, ["run", "--env-file", str(env_file), "--help"])
    assert result.exit_code == 0, result.output
    assert os.environ.get("ADDRESS") == ADDRESS


def test_env_file_multiple_files_b_wins(cli, runner, tmp_path, monkeypatch):
    env_file_a = write_env(
        tmp_path, ".env.a", mapping={"SHARED_VAR": "from_a", "UNIQUE_A": "value_a"}
    )
    env_file_b = write_env(
        tmp_path, ".env.b", mapping={"SHARED_VAR": "from_b", "UNIQUE_B": "value_b"}
    )
    result = runner.invoke(
        cli,
        ["run", "--env-file", str(env_file_a), "--env-file", str(env_file_b), "--help"],
    )
    assert result.exit_code == 0, result.output
    assert os.environ.get("SHARED_VAR") == "from_b"
    assert os.environ.get("UNIQUE_A") == "value_a"
    assert os.environ.get("UNIQUE_B") == "value_b"


def test_env_file_invalid_with_suggestions(cli, runner, tmp_path):
    write_env(tmp_path, ".env", content="VAR=value\n")
    write_env(tmp_path, ".env.local", content="VAR=value\n")
    write_env(tmp_path, "config.env", content="VAR=value\n")
    invalid_file = write_env(tmp_path, "config.json", content='{"test": "value"}\n')
    result = runner.invoke(cli, ["run", "--env-file", str(invalid_file)])
    assert result.exit_code != 0
    assert ("Invalid env file: " + invalid_file.name) in result.output or (
        "Invalid env file: " + str(invalid_file)
    ) in result.output
    assert "Did you mean:" in result.output
    assert ".env" in result.output
    assert ".env.local" in result.output
    assert "config.env" in result.output


def test_env_file_invalid_without_suggestions(cli, runner, tmp_path):
    invalid_file = write_env(tmp_path, "config.json", content='{"test": "value"}\n')
    result = runner.invoke(cli, ["run", "--env-file", str(invalid_file)])
    assert result.exit_code != 0
    assert "Refusing to load non-.env file:" in result.output
    assert (invalid_file.name in result.output) or (str(invalid_file) in result.output)
    assert "Allowed: '.env', '.env.<suffix>' and '<prefix>.env'." in result.output
    assert "Did you mean:" not in result.output
