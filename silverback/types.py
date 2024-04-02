from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum  # NOTE: `enum.StrEnum` only in Python 3.11+
from typing import Literal, Protocol, get_args

from pydantic import BaseModel, Field
from typing_extensions import Self  # Introduced 3.11


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


class BaseDatapoint(BaseModel):
    type: str  # discriminator

    # NOTE: default value ensures we don't have to set this manually
    time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


ScalarType = bool | int | float | Decimal
scalar_types = get_args(ScalarType)


class ScalarDatapoint(BaseDatapoint):
    type: Literal["scalar"] = "scalar"

    # NOTE: app-supported scalar value types:
    data: ScalarType


# This is what a Silverback app task must return to integrate properly with our data acq system
Metrics = dict[str, BaseDatapoint]
# Otherwise, log a warning and ignore any unconverted return value(s)
