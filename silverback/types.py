from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum  # NOTE: `enum.StrEnum` only in Python 3.11+
from typing import Literal

from ape.logging import get_logger
from pydantic import BaseModel, Field, RootModel, ValidationError, model_validator
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
            if isinstance(datapoints[name], dict) and "type" in datapoints[name]:
                try:
                    datapoints[name] = ScalarDatapoint.model_validate(datapoints[name])
                except ValidationError as e:
                    names_to_remove[name] = e
            elif not isinstance(datapoints[name], Datapoint):
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
