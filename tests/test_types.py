from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from silverback.types import CRON_CHECK_SECONDS, CronSchedule, Datapoints


@pytest.mark.parametrize(
    "cron_schedule,current_time_str",
    [
        ("5 0 * 8 *", "2024-08-01 00:05"),
        ("0 22 * * 1-5", "2024-06-03 22:00"),
        ("23 0-20/2 * * *", "2024-06-03 20:23"),
        ("0 0,12 1 */2 *", "2024-07-01 00:00"),
        ("0 4 8-14 * *", "2024-06-08 04:00"),
        ("0 0 1,15 * 3", "2024-06-05 00:00"),
    ],
)
def test_cron_is_ready(cron_schedule, current_time_str):
    current_time = datetime.fromisoformat(current_time_str)
    cron = CronSchedule(cron=cron_schedule)
    assert cron.is_ready(current_time)
    current_time += timedelta(seconds=CRON_CHECK_SECONDS)
    assert not cron.is_ready(current_time)


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
