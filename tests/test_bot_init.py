import pytest

from silverback import SilverbackBot
from silverback.exceptions import NoSignerLoaded


def test_signer_required_with_signer_available(create_settings, signer):
    bot = SilverbackBot(settings=create_settings(signer=signer), signer_required=True)
    assert bot.signer is signer


def test_signer_required_raises_when_no_signer(create_settings):
    with pytest.raises(NoSignerLoaded):
        SilverbackBot(settings=create_settings(), signer_required=True)


def test_signer_not_required_default(create_settings):
    bot = SilverbackBot(settings=create_settings())
    assert bot.signer is None
