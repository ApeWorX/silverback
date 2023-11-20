from typing import Optional, Protocol

from pydantic import BaseModel
from typing_extensions import Self  # Introduced 3.11


class SilverbackSettings(Protocol):
    INSTANCE: str
    PERSISTENCE_CLASS: Optional[str]
    PERSISTENCE_URI: Optional[str]

    def get_network_choice(self) -> str:
        ...


class SilverbackIdent(BaseModel):
    identifier: str
    network_choice: str

    @classmethod
    def from_settings(cls, settings_: SilverbackSettings) -> Self:
        return cls(identifier=settings_.INSTANCE, network_choice=settings_.get_network_choice())


class SilverbackStartupState(BaseModel):
    last_block_seen: int
    last_block_processed: int


def handler_id_block(block_number: Optional[int]) -> str:
    """Return a unique handler ID string for a block"""
    if block_number is None:
        return "block/pending"
    return f"block/{block_number}"


def handler_id_event(contract_address: str | None, event_signature: str) -> str:
    """Return a unique handler ID string for an event"""
    # TODO: Under what circumstance can address be None?
    return f"event/{contract_address or 'unknown'}/{event_signature}"
