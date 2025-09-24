import pytest

from silverback import SilverbackBot
from silverback.exceptions import NoSignerLoaded


def test_signer_required_with_signer_available(settings, signer):
    bot = SilverbackBot(settings=settings(signer), signer_required=True)
    assert bot.signer_required is True
    assert bot.signer is signer
    assert hasattr(bot.signer, "call")


def test_signer_required_raises_when_no_signer(settings):
    with pytest.raises(NoSignerLoaded):
        SilverbackBot(settings=settings(None), signer_required=True)


def test_signer_not_required_default(settings):
    bot = SilverbackBot(settings=settings(None))
    assert bot.signer_required is False
    assert bot.signer is None
