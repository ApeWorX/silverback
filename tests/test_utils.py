import pytest

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
