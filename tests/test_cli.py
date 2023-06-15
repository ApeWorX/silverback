def test_run_verbosity(cli, runner):
    """
    A test showing the verbosity option works.
    If it didn't work, the exit code would not be 0 here.
    """
    result = runner.invoke(cli, ["run", "--help", "--verbosity", "DEBUG"])
    assert result.exit_code == 0
