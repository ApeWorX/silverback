import enum
import math
import uuid
from datetime import datetime
from hashlib import blake2s
from typing import Annotated, Any

from pydantic import BaseModel, Field, field_validator, model_validator

# NOTE: All configuration settings must be uint8 integer values
UINT8_MAX = 2**8 - 1

TIME_FORMAT_STRING = "{0:%x} {0:%X}"


def render_dict_as_yaml(value: Any, prepend: str = "\n") -> str:
    if hasattr(value, "build_display_fields"):
        return render_dict_as_yaml(value.build_display_fields(), prepend=prepend)

    elif not isinstance(value, dict):
        raise ValueError(f"'{type(value)}' is not renderable.")

    return prepend.join(
        (
            f"{key}: {value}"
            if isinstance(value, str)
            else f"{key}:{prepend + '  '}{render_dict_as_yaml(value, prepend=(prepend + '  '))}"
        )
        for key, value in value.items()
    )


class WorkspaceInfo(BaseModel):
    id: uuid.UUID
    owner_id: uuid.UUID
    name: str
    slug: str


class ClusterConfiguration(BaseModel):
    """Configuration of the cluster (represented as 16 byte value)"""

    # NOTE: All defaults should be the minimal end of the scale,
    #       so that `__or__` works right

    # Version byte (Byte 1)
    version: int = 1

    # Bot Worker Configuration (Bytes 2-3)
    cpu: Annotated[int, Field(ge=0, le=16)] = 0  # 0.25 vCPU
    """Allocated vCPUs per bot: 0.25 vCPU (0) to 16 vCPU (6)"""

    memory: Annotated[int, Field(ge=0, le=120)] = 0  # 512 MiB
    """Total memory per bot (in GB)"""

    # Runner configuration (Bytes 4-6)
    networks: Annotated[int, Field(ge=1, le=20)] = 1
    """Maximum number of concurrent network runners"""

    bots: Annotated[int, Field(ge=1, le=250)] = 1
    """Maximum number of concurrent bots running"""

    triggers: Annotated[int, Field(ge=5, le=1000, multiple_of=5)] = 30
    """Maximum number of task triggers across all running bots"""

    # TODO: Recorder configuration
    # NOTE: Bytes 7-15 empty

    @field_validator("cpu", mode="before")
    def parse_cpu_value(cls, value: str | int) -> int:
        if not isinstance(value, str):
            return value

        return round(math.log2(float(value.split(" ")[0]) * 1024 / 256))

    @field_validator("memory", mode="before")
    def parse_memory_value(cls, value: str | int) -> int:
        if not isinstance(value, str):
            return value

        mem, units = value.split(" ")
        if units.lower() == "mib":
            assert mem == "512"
            return 0

        assert units.lower() == "gb"
        return int(mem)

    @classmethod
    def decode(cls, value: int) -> "ClusterConfiguration":
        """Decode the configuration from 16 byte integer value"""
        if isinstance(value, ClusterConfiguration):
            return value  # TODO: Something weird with SQLModel

        # NOTE: Do not change the order of these, these are not forwards compatible
        return cls(
            version=value & UINT8_MAX,
            cpu=(value >> 8) & UINT8_MAX,
            memory=(value >> 16) & UINT8_MAX,
            networks=(value >> 24) & UINT8_MAX,
            bots=(value >> 32) & UINT8_MAX,
            triggers=5 * ((value >> 40) & UINT8_MAX),
        )

    def encode(self) -> int:
        """Encode configuration as 16 byte integer value"""
        # NOTE: Do not change the order of these, these are not forwards compatible
        return (
            self.version
            + (self.cpu << 8)
            + (self.memory << 16)
            + (self.networks << 24)
            + (self.bots << 32)
            + (self.triggers // 5 << 40)
        )

    def build_display_fields(self) -> dict[str, str]:
        return dict(
            cpu=f"{256 * 2 ** self.cpu / 1024} vCPU",
            memory=(f"{self.memory} GB" if self.memory > 0 else "512 MiB"),
            networks=str(self.networks),
            bots=str(self.bots),
            triggers=str(self.triggers),
        )


class ClusterTier(enum.IntEnum):
    """Suggestions for different tier configurations"""

    PERSONAL = ClusterConfiguration(
        cpu="0.25 vCPU",
        memory="512 MiB",
        networks=3,
        bots=5,
        triggers=30,
    ).encode()
    PROFESSIONAL = ClusterConfiguration(
        cpu="1 vCPU",
        memory="2 GB",
        networks=10,
        bots=20,
        triggers=120,
    ).encode()

    def configuration(self) -> ClusterConfiguration:
        return ClusterConfiguration.decode(int(self))


class ClusterStatus(enum.IntEnum):
    # NOTE: Selected integer values with some space for other steps
    CREATED = 0  # User record created, but not paid for yet
    STANDUP = 3  # Payment received, provisioning infrastructure
    RUNNING = 5  # Paid for and fully deployed by payment handler
    TEARDOWN = 6  # User triggered shutdown or payment expiration recorded
    REMOVED = 9  # Infrastructure de-provisioning complete

    def __str__(self) -> str:
        return self.name.capitalize()


class ClusterInfo(BaseModel):
    # NOTE: Raw API object (gets exported)
    id: uuid.UUID  # NOTE: Keep this private, used as a temporary secret key for payment
    name: str
    slug: str
    configuration: ClusterConfiguration

    created: datetime
    status: ClusterStatus
    last_updated: datetime

    def build_display_fields(self) -> dict[str, str | dict[str, str]]:
        return dict(
            # No `.id`, not visible to client user
            name=self.name,
            # No `.slug`, primary identifier used in dict
            # NOTE: Convert local time
            created=TIME_FORMAT_STRING.format(self.created.astimezone()),
            last_updated=TIME_FORMAT_STRING.format(self.last_updated.astimezone()),
            status=str(self.status),
            configuration=self.configuration.build_display_fields(),
        )


# TODO: Merge `/health` with `/`
class ClusterState(BaseModel):
    """
    Cluster Build Information and Configuration, direct from cluster control service
    """

    version: str = Field(alias="cluster_version")  # TODO: Rename in cluster
    configuration: ClusterConfiguration | None = None  # TODO: Add to cluster
    # TODO: Add other useful summary fields for frontend use (`bots: int`, `errors: int`, etc.)


class BotInfo(BaseModel):
    id: uuid.UUID  # TODO: Change `.instance_id` field to `id: UUID`

    # TODO: Add `.network`, `.slug`, `.network` fields to cluster model
    @model_validator(mode="before")
    def set_expected_fields(cls, data: dict) -> dict:
        instance_id: str = data.get("instance_id", "random:network:<unknown>")
        name_hash = blake2s(instance_id.encode("utf-8"))
        data["id"] = uuid.UUID(bytes=name_hash.digest()[:16])
        ecosystem, network, name = instance_id.split(":")
        data["slug"] = name
        data["name"] = name.capitalize()
        data["network"] = f"{ecosystem}:{network}"
        return data

    slug: str
    name: str
    network: str

    # TODO: More config fields (`.description`, `.image`, `.account`, `.environment`)

    # Other fields that are currently in there (TODO: Remove)
    config_set_name: str
    config_set_revision: int
    revision: int
    terminated: bool

    def build_display_fields(self) -> dict[str, str]:
        return dict(
            # No `.id`, not visible to client user
            # No `.slug`, primary identifier used in dict
            name=self.name,
            network=self.network,
        )
