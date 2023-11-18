from enum import Enum
from typing import Annotated, Any, Dict, Optional
from typing_extensions import Self  # Introduced 3.11

from pydantic import BaseModel

from .settings import Settings


class SilverbackIdent(BaseModel):
    identifier: str
    network_choice: str

    @classmethod
    def from_settings(cls, settings_: Settings) -> Self:
        return cls(identifier=settings_.INSTANCE, network_choice=settings_.get_network_choice())


def handler_id_block(block_number: Optional[int]) -> str:
    """Return a unique handler ID string for a block"""
    if block_number is None:
        return "block/pending"
    return f"block/{block_number}"


def handler_id_event(contract_address: str | None, event_signature: str) -> str:
    """Return a unique handler ID string for an event"""
    # TODO: Under what circumstance can address be None?
    return f"event/{contract_address or 'unknown'}/{event_signature}"
