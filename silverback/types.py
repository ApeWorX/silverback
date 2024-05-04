from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum  # NOTE: `enum.StrEnum` only in Python 3.11+
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic.functional_serializers import PlainSerializer
from typing_extensions import Annotated, get_args


class TaskType(str, Enum):
    STARTUP = "startup"
    NEW_BLOCKS = "block"
    EVENT_LOG = "event"
    SHUTDOWN = "shutdown"

    def __str__(self) -> str:
        return self.value


class SilverbackID(BaseModel):
    name: str
    ecosystem: str
    network: str


def iso_format(dt: datetime) -> str:
    return dt.isoformat()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


UTCTimestamp = Annotated[
    datetime,
    # TODO: Bug in TaskIQ can't serialize `datetime`
    PlainSerializer(iso_format, return_type=str),
]


class _BaseDatapoint(BaseModel):
    type: str  # discriminator


# NOTE: Maximum supported parquet integer type: https://parquet.apache.org/docs/file-format/types
INT96_RANGE = (-(2**95), 2**95 - 1)
Int96 = Annotated[int, Field(ge=INT96_RANGE[0], le=INT96_RANGE[1])]
# NOTE: only these types of data are implicitly converted e.g. `{"something": 1, "else": 0.001}`
ScalarType = bool | Int96 | float | Decimal
SCALAR_TYPES = tuple(t.__origin__ if hasattr(t, "__origin__") else t for t in get_args(ScalarType))


def is_scalar_type(val: Any) -> bool:
    return isinstance(val, SCALAR_TYPES)


class ScalarDatapoint(_BaseDatapoint):
    type: Literal["scalar"] = "scalar"
    data: ScalarType


# NOTE: Other datapoint types must be explicitly used

# TODO: Other datapoint types added to union here...
Datapoint = ScalarDatapoint
