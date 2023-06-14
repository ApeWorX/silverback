import pytest
from click.testing import CliRunner

from silverback._cli import cli as root_cli


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def cli():
    return root_cli
