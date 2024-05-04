from decimal import Decimal

import pytest

from silverback.types import Datapoints


@pytest.mark.parametrize(
    "raw_return,expected",
    [
        # String datapoints don't parse (empty datapoints)
        ({"a": "b"}, {}),
        # ints parse
        ({"a": 1}, {"a": {"type": "scalar", "data": 1}}),
        # max INT96 value
        (
            {"a": 2**96 - 1},
            {"a": {"type": "scalar", "data": 79228162514264337593543950335}},
        ),
        # int over INT96 max parses as Decimal
        (
            {"a": 2**96},
            {"a": {"type": "scalar", "data": Decimal("79228162514264337593543950336")}},
        ),
        # Decimal parses as Decimal
        (
            {"a": Decimal("1e12")},
            {"a": {"type": "scalar", "data": Decimal("1000000000000")}},
        ),
        # float parses as float
        (
            {"a": 1e12},
            {"a": {"type": "scalar", "data": 1000000000000.0}},
        ),
    ],
)
def test_datapoint_parsing(raw_return, expected):
    assert Datapoints(root=raw_return).model_dump() == expected
