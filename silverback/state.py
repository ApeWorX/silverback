from pathlib import Path
from typing import Any

from ape.logging import get_logger
from pydantic import BaseModel, Field, field_validator

from .types import ScalarType, SilverbackID, UTCTimestamp, is_scalar_type, utc_now

logger = get_logger(__name__)


class StateSnapshot(BaseModel):
    # Last time the state was updated
    # NOTE: intended to use default when creating a model with this type
    last_updated: UTCTimestamp = Field(default_factory=utc_now)

    # Stored parameters from last session
    parameters: dict[str, ScalarType] = {}

    @field_validator("parameters", mode="before")
    def parse_parameters(cls, parameters: dict) -> dict:
        # NOTE: Filter out any values that we cannot serialize
        successfully_parsed_parameters = {}
        for param_name, param_value in parameters.items():
            if is_scalar_type(param_value):
                successfully_parsed_parameters[param_name] = param_value
            else:
                logger.error(
                    f"Cannot backup '{param_name}' of type '{type(param_value)}': {param_value}"
                )

        return successfully_parsed_parameters

    @property
    def last_block_seen(self) -> int:
        # Last block number seen by runner
        return self.parameters.get("system:last_block_seen", -1)  # type: ignore[return-value]

    @property
    def last_block_processed(self) -> int:
        # Last block number processed by a worker
        return self.parameters.get("system:last_block_processed", -1)  # type: ignore[return-value]

    def __dir__(self) -> list[str]:
        return [
            *(param for param in self.parameters if "system:" not in param),
            "last_block_processed",
            "last_block_seen",
        ]

    def __getattr__(self, attr: str) -> Any:
        try:
            return super().__getattr__(attr)  # type: ignore[misc]
        except AttributeError:
            return self.parameters.get(attr)


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
        self.state_backup_file.write_text(snapshot.model_dump_json())
