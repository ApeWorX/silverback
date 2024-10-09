from pathlib import Path

from pydantic import BaseModel, Field

from .types import SilverbackID, UTCTimestamp, utc_now


class StateSnapshot(BaseModel):
    # Last block number seen by runner
    last_block_seen: int

    # Last block number processed by a worker
    last_block_processed: int

    # Last time the state was updated
    # NOTE: intended to use default when creating a model with this type
    last_updated: UTCTimestamp = Field(default_factory=utc_now)


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
            StateSnapshot.parse_file(self.state_backup_file)
            if self.state_backup_file.exists()
            else None
        )

    async def save(self, snapshot: StateSnapshot):
        self.state_backup_file.write_text(snapshot.model_dump_json())
