from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum  # NOTE: `enum.StrEnum` only in Python 3.11+
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, Field
from pydantic.functional_serializers import PlainSerializer
from taskiq import Context, TaskiqDepends, TaskiqState
from typing_extensions import Self  # Introduced 3.11


def iso_format(dt: datetime) -> str:
    return dt.isoformat()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


UTCTimestamp = Annotated[
    datetime,
    # TODO: Bug in TaskIQ can't serialize `datetime`
    PlainSerializer(iso_format, return_type=str),
]


class TaskType(str, Enum):
    STARTUP = "silverback_startup"  # TODO: Shorten in 0.4.0
    NEW_BLOCKS = "block"
    EVENT_LOG = "event"
    SHUTDOWN = "silverback_shutdown"  # TODO: Shorten in 0.4.0

    def __str__(self) -> str:
        return self.value


class ISilverbackSettings(Protocol):
    """Loose approximation of silverback.settings.Settings.  If you can, use the class as
    a type reference."""

    INSTANCE: str
    RECORDER_CLASS: str | None

    def get_network_choice(self) -> str:
        ...


class SilverbackID(BaseModel):
    identifier: str
    network_choice: str

    @classmethod
    def from_settings(cls, settings_: ISilverbackSettings) -> Self:
        return cls(identifier=settings_.INSTANCE, network_choice=settings_.get_network_choice())


class SilverbackStartupState(BaseModel):
    last_block_seen: int
    last_block_processed: int






class _BaseDatapoint(BaseModel):
    type: str  # discriminator


# NOTE: only these types of data are implicitly converted e.g. `{"something": 1, "else": 0.001}`
ScalarType = bool | int | float | Decimal


class ScalarDatapoint(_BaseDatapoint):
    type: Literal["scalar"] = "scalar"
    data: ScalarType


# NOTE: Other datapoint types must be explicitly used

# TODO: Other datapoint types added to union here...
Datapoint = ScalarDatapoint
