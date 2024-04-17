from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum  # NOTE: `enum.StrEnum` only in Python 3.11+
from typing import Literal, Union

from pydantic import BaseModel, Field
from pydantic.functional_serializers import PlainSerializer
from taskiq import Context, TaskiqDepends, TaskiqState
from typing_extensions import Annotated  # Introduced 3.9


class TaskType(str, Enum):
    STARTUP = "silverback_startup"  # TODO: Shorten in 0.4.0
    NEW_BLOCKS = "block"
    EVENT_LOG = "event"
    SHUTDOWN = "silverback_shutdown"  # TODO: Shorten in 0.4.0

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


class AppState(BaseModel):
    # Last block number seen by runner
    last_block_seen: int

    # Last block number processed by a worker
    last_block_processed: int

    # Last time the state was updated
    # NOTE: intended to use default when creating a model with this type
    last_updated: UTCTimestamp = Field(default_factory=utc_now)


def get_worker_state(context: Annotated[Context, TaskiqDepends()]) -> TaskiqState:
    return context.state


WorkerState = Annotated[TaskiqState, TaskiqDepends(get_worker_state)]


class _BaseDatapoint(BaseModel):
    type: str  # discriminator


# NOTE: only these types of data are implicitly converted e.g. `{"something": 1, "else": 0.001}`
ScalarType = Union[bool, int, float, Decimal]


class ScalarDatapoint(_BaseDatapoint):
    type: Literal["scalar"] = "scalar"
    data: ScalarType


# NOTE: Other datapoint types must be explicitly used

# TODO: Other datapoint types added to union here...
Datapoint = ScalarDatapoint
