from datetime import datetime, timedelta
from typing import Optional, Protocol

import pycron  # type: ignore[import]
from eth_utils import keccak
from pydantic import BaseModel
from typing_extensions import Self  # Introduced 3.11

ONE_MINUTE = timedelta(minutes=1)


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


class CronJob(BaseModel):
    last_run: datetime = datetime.utcnow()
    schedule: str

    @property
    def schedule_id(self) -> str:
        return string_4byte(self.schedule)

    @property
    def task_name(self) -> str:
        return f"cron/{self.schedule_id}"

    def mark_ran(self):
        self.last_run = datetime.utcnow()

    def should_run(self) -> bool:
        # NOTE: Checking against ONE_MINUTE since that is the resolution of cron schedules
        return pycron.is_now(self.schedule) and datetime.utcnow() - self.last_run >= ONE_MINUTE


def string_4byte(s: str):
    """Return a 4-byte hex string hash of the given string"""
    return keccak(text=s).hex()[:8]


def handler_id_block(block_number: Optional[int]) -> str:
    """Return a unique handler ID string for a block"""
    if block_number is None:
        return "block/pending"
    return f"block/{block_number}"


def handler_id_event(contract_address: Optional[str], event_signature: str) -> str:
    """Return a unique handler ID string for an event"""
    # TODO: Under what circumstance can address be None?
    return f"{contract_address or 'unknown'}/event/{event_signature}"
