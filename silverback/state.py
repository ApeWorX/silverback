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


class AppDatastore:
    """
    Very basic implementation used to store application state and handler result data by
    storing/retreiving state from a JSON-encoded file.

    The file structure that this Recorder uses leverages the value of `SILVERBACK_APP_NAME`
    as well as the configured network to determine the location where files get saved:

        ./.silverback-sessions/
          <app-name>/
            <network choice>/
              state.json  # always write here

    Note that this format can be read by basic means (even in a JS frontend):

    You may also want to give your app a unique name so the data does not get overwritten,
    if you are using multiple apps from the same directory:

    - `SILVERBACK_APP_NAME`: Any alphabetical string valid as a folder name
    """

    async def init(self, app_id: SilverbackID) -> StateSnapshot | None:
        data_folder = (
            Path.cwd() / ".silverback-sessions" / app_id.name / app_id.ecosystem / app_id.network
        )
        data_folder.mkdir(parents=True, exist_ok=True)

        self.state_backup_file = data_folder / "state.json"

        return (
            StateSnapshot.parse_file(self.state_backup_file)
            if self.state_backup_file.exists()
            else None
        )

    async def save(self, snapshot: StateSnapshot):
        if self.state_backup_file.exists():
            old_snapshot = AppState.parse_file(self.snapshot_backup_file)
            if old_snapshot.last_block_seen > snapshot.last_block_seen:
                snapshot.last_block_seen = old_snapshot.last_block_seen
            if old_snapshot.last_block_processed > snapshot.last_block_processed:
                snapshot.last_block_processed = old_snapshot.last_block_processed

        snapshot.last_updated = utc_now()
        self.state_backup_file.write_text(snapshot.model_dump_json())
