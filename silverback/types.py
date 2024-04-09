from enum import Enum
from typing import Optional, Protocol

from pydantic import BaseModel
from typing_extensions import Self  # Introduced 3.11


class TaskType(str, Enum):
    STARTUP = "silverback_startup"  # TODO: Shorten
    NEW_BLOCKS = "block"
    EVENT_LOG = "event"
    SHUTDOWN = "silverback_shutdown"  # TODO: Shorten

    def __str__(self) -> str:
        return self.value


class ISilverbackSettings(Protocol):
    """Loose approximation of silverback.settings.Settings.  If you can, use the class as
    a type reference."""

    INSTANCE: str
    PERSISTENCE_CLASS: Optional[str]

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
