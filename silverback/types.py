from abc import ABC, abstractmethod
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum  # NOTE: `enum.StrEnum` only in Python 3.11+
from typing import Any, Literal, get_args

from ape.logging import get_logger
from pydantic import BaseModel, Field, RootModel, model_validator
from pydantic.functional_serializers import PlainSerializer
from typing_extensions import Annotated

logger = get_logger(__name__)


class TaskType(str, Enum):
    # System-only Tasks
    SYSTEM_CONFIG = "system:config"
    SYSTEM_USER_TASKDATA = "system:user-taskdata"
    SYSTEM_USER_ALL_TASKDATA = "system:user-all-taskdata"
    SYSTEM_LOAD_SNAPSHOT = "system:load-snapshot"
    SYSTEM_CREATE_SNAPSHOT = "system:create-snapshot"
    SYSTEM_SET_PARAM = "system:set-param"
    SYSTEM_SET_PARAM_BATCH = "system:batch-param"

    # User-accessible Tasks
    STARTUP = "user:startup"
    CRON_JOB = "user:cron-job"
    NEW_BLOCK = "user:new-block"
    EVENT_LOG = "user:event-log"
    METRIC_VALUE = "user:metric-value"
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


class _BaseDatapoint(BaseModel, ABC):
    type: str  # discriminator

    @abstractmethod
    def render(self) -> str:
        """Render Datapoint for viewing in logs"""

    @abstractmethod
    def as_row(self) -> "ScalarType | dict":
        """Convert into a type suitable for a dataframe"""


# NOTE: Maximum supported parquet integer type: https://parquet.apache.org/docs/file-format/types
Int96 = Annotated[int, Field(ge=-(2**95), le=2**95 - 1)]
# NOTE: only these types of data are implicitly converted e.g. `{"something": 1, "else": 0.001}`
ScalarType = bool | Int96 | float | Decimal
# NOTE: Interesting side effect is that `int` outside the INT96 range parse as `Decimal`
#       This is okay, preferable actually, because it means we can store ints outside that range


def is_scalar_type(val: Any) -> bool:
    """Check if `val` is a `ScalarType` type"""
    return any(
        isinstance(val, d_type.__origin__ if hasattr(d_type, "__origin__") else d_type)
        for d_type in get_args(ScalarType)
    )


class ScalarDatapoint(_BaseDatapoint):
    type: Literal["scalar"] = "scalar"
    data: ScalarType

    def render(self) -> str:
        return str(self.data)

    def as_row(self) -> ScalarType:
        return self.data


class ParamChange(_BaseDatapoint):
    type: Literal["setparam"] = "setparam"
    old: ScalarType | None
    new: ScalarType

    def render(self) -> str:
        return str(self.as_row())

    def as_row(self) -> dict:
        return dict(old=self.old, new=self.new)


# NOTE: Other datapoint types must be explicitly defined as subclasses of `_BaseDatapoint`
#       Users will have to import and use these directly

# NOTE: Other datapoint types must be added to this union
Datapoint = ScalarDatapoint | ParamChange


def is_datapoint(val: Any) -> bool:
    """`val` is a `Datapoint` type"""
    return any(isinstance(val, d_type) for d_type in get_args(Datapoint))


class Datapoints(RootModel):
    root: dict[str, Datapoint]

    @model_validator(mode="before")
    def parse_datapoints(cls, datapoints: dict) -> dict:
        successfully_parsed_datapoints = {}
        for name, datapoint in datapoints.items():
            if is_datapoint(datapoint):
                successfully_parsed_datapoints[name] = datapoint

            elif is_scalar_type(datapoint):
                # Automatically convert raw scalar types into datapoints
                successfully_parsed_datapoints[name] = ScalarDatapoint(data=datapoints[name])

            else:
                # Prune and raise a warning about unconverted datapoints
                logger.warning(
                    f"Cannot convert datapoint '{name}' of type '{type(datapoint)}': {datapoint}"
                )

        return successfully_parsed_datapoints

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
