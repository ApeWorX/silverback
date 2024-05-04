from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Iterator

from ape.logging import get_logger
from pydantic import BaseModel, Field
from taskiq import TaskiqResult
from typing_extensions import Self  # Introduced 3.11

from .types import Datapoints, SilverbackID, UTCTimestamp, iso_format, utc_now

logger = get_logger(__name__)


class TaskResult(BaseModel):
    # NOTE: Model must eventually serialize using PyArrow/Parquet for long-term storage

    # Task Info
    task_name: str
    execution_time: float
    error: str | None = None

    # NOTE: intended to use default when creating a model with this type
    completed: UTCTimestamp = Field(default_factory=utc_now)

    # System Metrics here (must default to None in case they are missing)
    block_number: int | None = None

    # Custom user metrics here
    metrics: Datapoints

    @classmethod
    def _extract_custom_metrics(cls, return_value: Any, task_name: str) -> Datapoints:
        if return_value is None:
            return Datapoints(root={})

        elif not isinstance(return_value, dict):
            return_value = {"return_value": return_value}

        return Datapoints(root=return_value)

    @classmethod
    def _extract_system_metrics(cls, labels: dict) -> dict:
        metrics = {}

        if block_number := labels.get("block_number"):
            metrics["block_number"] = int(block_number)

        return metrics

    @classmethod
    def from_taskiq(
        cls,
        result: TaskiqResult,
    ) -> Self:
        task_name = result.labels.pop("task_name", "<unknown>")
        return cls(
            task_name=task_name,
            execution_time=result.execution_time,
            error=str(result.error) if result.error else None,
            metrics=cls._extract_custom_metrics(result.return_value, task_name),
            **cls._extract_system_metrics(result.labels),
        )


class BaseRecorder(ABC):
    """
    Base class used for serializing task results to an external data recording process.

    Recorders are configured using the following environment variable:

    - `SILVERBACK_RECORDER_CLASS`: Any fully qualified subclass of `BaseRecorder` as a string
    """

    @abstractmethod
    async def init(self, app_id: SilverbackID):
        """
        Handle any async initialization from Silverback settings (e.g. migrations).
        """

    @abstractmethod
    async def add_result(self, result: TaskResult):
        """Store a result for a Silverback instance's handler"""


class JSONLineRecorder(BaseRecorder):
    """
    Very basic implementation of BaseRecorder used to handle results by appending to a file
    containing newline-separated JSON entries (https://jsonlines.org/).

    The file structure that this Recorder uses leverages the value of `SILVERBACK_APP_NAME`
    as well as the configured network to determine the location where files get saved:

        ./.silverback-sessions/
          <app-name>/
            <network choice>/
              session-<timestamp>.json  # start time of each app session

    Each app "session" (everytime the Runner is started up via `silverback run`) is recorded
    in a separate file with the timestamp of the first handled task in its filename.

    Note that this format can be read by basic means (even in a JS frontend), or read
    efficiently via Apache Arrow for more efficient big data processing:

        https://arrow.apache.org/docs/python/json.html

    Usage:

    To use this recorder, you must configure the following environment variable:

    - `SILVERBACK_RECORDER_CLASS`: `"silverback.recorder:JSONLineRecorder"`

    You may also want to give your app a unique name so the data does not get overwritten,
    if you are using multiple apps from the same directory:

    - `SILVERBACK_APP_NAME`: Any alphabetical string valid as a folder name
    """

    async def init(self, app_id: SilverbackID):
        data_folder = (
            Path.cwd() / ".silverback-sessions" / app_id.name / app_id.ecosystem / app_id.network
        )
        data_folder.mkdir(parents=True, exist_ok=True)

        self.session_results_file = data_folder / f"session-{iso_format(utc_now())}.jsonl"

    async def add_result(self, result: TaskResult):
        # NOTE: mode `a` means "append to file if exists"
        # NOTE: JSONNL convention requires the use of `\n` as newline char
        with self.session_results_file.open("a") as writer:
            writer.write(result.model_dump_json())
            writer.write("\n")


def get_metrics(session: Path, task_name: str) -> Iterator[dict]:
    """
    Useful function for fetching results and loading them for display.
    """
    with open(session, "r") as file:
        for line in file:
            if (
                (result := TaskResult.model_validate_json(line))
                and result.task_name == task_name
                and not result.error
            ):
                yield {
                    "block_number": result.block_number,
                    "execution_time": result.execution_time,
                    "completed": result.completed,
                    **{name: datapoint.data for name, datapoint in result.metrics.items()},
                }
