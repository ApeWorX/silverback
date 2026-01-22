from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .types import ScalarType, SilverbackID, UTCTimestamp, utc_now


class StateSnapshot(BaseModel):
    # Last block number seen by runner
    last_block_seen: int

    # Last block number processed by a worker
    last_block_processed: int

    # NOTE: Any new items we add here must have a default to be backwards-compatible

    # Last nonce used by signer
    last_nonce_used: int | None = None

    # User-defined parameters
    parameters: dict[str, ScalarType] = {}

    # Last time the state was updated
    # NOTE: intended to use default when creating a model with this type
    last_updated: UTCTimestamp = Field(default_factory=utc_now)

    def __dir__(self) -> list[str]:
        return sorted([*super().__dir__(), *self.parameters])

    def __getattr__(self, attr: str) -> Any:
        try:
            return super().__getattr__(attr)  # type: ignore[misc]
        except AttributeError:
            if val := self.parameters.get(attr):
                return val

        raise AttributeError(f"'{self.__class__.__qualname__}' object has no attribute '{attr}'")


class Datastore:
    """
    Very basic implementation used to store bot state and handler result data by
    storing/retreiving state from a JSON-encoded file.

    The file structure that this Recorder uses leverages the value of `SILVERBACK_BOT_NAME`
    as well as the configured network to determine the location where files get saved:

        ./.silverback-sessions/
          <bot-name>/
            <network choice>/
              state.json  # always write here

    Note that this format can be read by basic means (even in a JS frontend):

    You may also want to give your bot a unique name so the data does not get overwritten,
    if you are using multiple bots from the same directory:

    - `SILVERBACK_BOT_NAME`: Any alphabetical string valid as a folder name
    """

    async def init(self, bot_id: SilverbackID) -> StateSnapshot | None:
        data_folder = (
            Path.cwd() / ".silverback-sessions" / bot_id.name / bot_id.ecosystem / bot_id.network
        )
        data_folder.mkdir(parents=True, exist_ok=True)
        self.state_backup_file = data_folder / "state.json"

        return (
            StateSnapshot.model_validate_json(self.state_backup_file.read_text())
            if self.state_backup_file.exists()
            else None
        )

    async def save(self, snapshot: StateSnapshot):
        self.state_backup_file.write_text(snapshot.model_dump_json())
