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
