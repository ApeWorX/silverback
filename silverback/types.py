from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum  # NOTE: `enum.StrEnum` only in Python 3.11+
from typing import Literal

from ape.logging import get_logger
from pydantic import BaseModel, Field, RootModel, ValidationError, model_serializer, model_validator
from pydantic.functional_serializers import PlainSerializer
from typing_extensions import Annotated

logger = get_logger(__name__)


class TaskType(str, Enum):
    # System-only Tasks
    SYSTEM_CONFIG = "system:config"
    SYSTEM_USER_TASKDATA = "system:user-taskdata"
    SYSTEM_USER_ALL_TASKDATA = "system:user-all-taskdata"

    # User-accessible Tasks
    STARTUP = "user:startup"
    NEW_BLOCK = "user:new-block"
    EVENT_LOG = "user:event-log"
    CRON_JOB = "user:cron-job"
    SHUTDOWN = "user:shutdown"

    def __str__(self) -> str:
        return self.value


class SilverbackID(BaseModel):
    name: str
    ecosystem: str
    network: str


def iso_format(dt: datetime) -> str:
    return dt.isoformat()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


UTCTimestamp = Annotated[
    datetime,
    # TODO: Bug in TaskIQ can't serialize `datetime`
    PlainSerializer(iso_format, return_type=str),
]


CRON_CHECK_SECONDS = 5


class CronSchedule(BaseModel):
    minute: str
    hour: str
    day_month: str
    month: str
    day_week: str

    def __init__(self, cron: str = "", **field_values):
        if cron:
            field_values = dict(zip(self.model_fields, cron.split(" ")))

        super().__init__(**field_values)

    @model_serializer
    def create_cron_string(self) -> str:
        return " ".join(map(lambda f: getattr(self, f), self.model_fields))

    def __str__(self) -> str:
        return self.create_cron_string()

    def _check_value(self, val: str, current: int) -> bool:
        if "/" in val:
            val, step_str = val.split("/")
            step = int(step_str)

        else:
            step = 1

        if "-" in val:
            start, stop = map(int, val.split("-"))
            matches = list(range(start, stop + 1, step))

        elif "," in val:
            matches = list(map(int, val.split(",")))

        elif val == "*":
            return current % step == step - 1

        else:
            matches = [int(val)]

        return current in matches

    def is_ready(self, current_time: datetime) -> bool:
        return all(
            [
                abs(current_time.second) < CRON_CHECK_SECONDS,  # NOTE: Ensure close to :00 seconds
                self._check_value(self.minute, current_time.minute),
                self._check_value(self.hour, current_time.hour),
                self._check_value(self.day_month, current_time.day),
                self._check_value(self.month, current_time.month),
                self._check_value(self.day_week, current_time.weekday() + 1),
            ]
        )


class _BaseDatapoint(BaseModel):
    type: str  # discriminator


# NOTE: Maximum supported parquet integer type: https://parquet.apache.org/docs/file-format/types
Int96 = Annotated[int, Field(ge=-(2**95), le=2**95 - 1)]
# NOTE: only these types of data are implicitly converted e.g. `{"something": 1, "else": 0.001}`
ScalarType = bool | Int96 | float | Decimal
# NOTE: Interesting side effect is that `int` outside the INT96 range parse as `Decimal`
#       This is okay, preferable actually, because it means we can store ints outside that range


class ScalarDatapoint(_BaseDatapoint):
    type: Literal["scalar"] = "scalar"
    data: ScalarType


# NOTE: Other datapoint types must be explicitly defined as subclasses of `_BaseDatapoint`
#       Users will have to import and use these directly

# NOTE: Other datapoint types must be added to this union
Datapoint = ScalarDatapoint


class Datapoints(RootModel):
    root: dict[str, Datapoint]

    @model_validator(mode="before")
    def parse_datapoints(cls, datapoints: dict) -> dict:
        names_to_remove: dict[str, ValidationError] = {}
        # Automatically convert raw scalar types
        for name in datapoints:
            if not isinstance(datapoints[name], Datapoint):
                try:
                    datapoints[name] = ScalarDatapoint(data=datapoints[name])
                except ValidationError as e:
                    names_to_remove[name] = e

        # Prune and raise a warning about unconverted datapoints
        for name in names_to_remove:
            data = datapoints.pop(name)
            logger.warning(
                f"Cannot convert datapoint '{name}' of type '{type(data)}': {names_to_remove[name]}"
            )

        return datapoints

    # Add dict methods
    def get(self, key: str, default: Datapoint | None = None) -> Datapoint | None:
        if key in self:
            return self[key]

        return default

    def __iter__(self):
        return iter(self.root)

    def __getitem__(self, item):
        return self.root[item]

    def items(self):
        return self.root.items()
