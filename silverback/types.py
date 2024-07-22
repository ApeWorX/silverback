from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum  # NOTE: `enum.StrEnum` only in Python 3.11+
from typing import Any, Literal

from ape.contracts import ContractEvent
from ape.logging import get_logger
from ape.types import HexBytes
from ape.utils import ManagerAccessMixin
from ape.utils.abi import StructParser
from eth_utils import keccak
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


class EventFilterArgs(RootModel, ManagerAccessMixin):
    """Represents input that should be used when filtering"""

    # TODO: Migrate this back to Ape

    root: list[HexBytes | list[HexBytes] | None] = Field(min_length=1, max_length=4)
    # NOTE: len > 4 should never happen

    @classmethod
    def from_event_and_filter_args(cls, event: ContractEvent, filter_args: dict[str, Any]):
        parser = StructParser(event.abi)
        root: list[HexBytes | list[HexBytes] | None] = [
            HexBytes(keccak(text=event.abi.selector))  # Full 32 bytes for event_id
        ]
        for arg in event.abi.inputs:
            assert arg.name, "Corrupted ABI"
            if len(filter_args) == 0:
                break  # No need to add more filter args to list, used them all

            elif not arg.indexed:
                if arg.name in filter_args:
                    raise ValueError(f"'{event.name}.{arg.name}' cannot be used for filtering.")

                continue  # otherwise skip it

            elif value := filter_args.pop(arg.name, None):
                py_type = cls.provider.network.ecosystem.get_python_types(arg)

                if isinstance(value, dict):
                    ls_values = list(value.values())
                    encoded_values = cls.conversion_manager.convert(ls_values, py_type)
                    converted_value = parser.decode_input([encoded_values])

                # TODO: How to handle OR filters?
                elif isinstance(value, (list, tuple)):
                    converted_value = parser.decode_input(value)

                else:
                    converted_value = cls.conversion_manager.convert(value, py_type)

                root.append(converted_value)

            else:
                # This means wildcard, but there are more args to add in the filter after it
                root.append(None)

        if unsupported_args := ", ".join(filter_args):
            raise ValueError(f"Arg(s) not in '{event.name}': {unsupported_args}")

        return cls(root=root)


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
