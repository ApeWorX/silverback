from typing import Any, Dict, Optional, Protocol

from ape.contracts import ContractEvent
from eth_utils import keccak
from pydantic import BaseModel
from typing_extensions import Self  # Introduced 3.11

EMPTY_HASH = "c5d24601"


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


class EventInputFilter:
    """Represents unique event input filtering"""

    filter_inputs: Dict[str, Any]

    def __init__(self, input_args):
        self.filter_inputs = input_args

    def __repr__(self):
        return f"EventInputFilter({self.filter_inputs})"

    @property
    def filter_id(self) -> str:
        """Unique ID string for the filter"""
        filter_str = ""
        for k in sorted(self.filter_inputs.keys()):
            filter_str += f"{k}={self.filter_inputs[k]}"
        return keccak(text=filter_str).hex()[:8]

    @classmethod
    def from_on_args(cls, container: ContractEvent, kwargs: Dict[str, Any]) -> Optional[Self]:
        """Init an input filter from optional kwargs given to an app.on_() decorator"""
        inputs = [i.name for i in container.abi.inputs]
        if not inputs:
            return None
        return cls({k: v for k, v in kwargs.items() if k in inputs})

    def matches(self, event: ContractEvent) -> bool:
        """Check if a given event matches this filter"""
        for k, v in self.filter_inputs.items():
            if getattr(event, k) != v:
                return False
        return True


def handler_id_block(block_number: Optional[int]) -> str:
    """Return a unique handler ID string for a block"""
    if block_number is None:
        return "block/pending"
    return f"block/{block_number}"


def handler_id_event(contract_address: Optional[str], event_signature: str) -> str:
    """Return a unique handler ID string for an event"""
    # TODO: Under what circumstance can address be None?
    return f"{contract_address or 'unknown'}/event/{event_signature}"
