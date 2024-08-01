import enum
import math
import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, computed_field, field_validator


class WorkspaceInfo(BaseModel):
    id: uuid.UUID
    owner_id: uuid.UUID
    name: str
    slug: str


class ClusterConfiguration(BaseModel):
    """Configuration of the cluster (represented as 16 byte value)"""

    # NOTE: This configuration must be encode-able to a uint64 value for db storage
    #       and on-chain processing through ApePay

    # NOTE: All defaults should be the minimal end of the scale,
    #       so that `__or__` works right

    # Version byte (Byte 0)
    # NOTE: Just in-case we change this after release
    version: int = 1

    # Bot Worker Configuration (Bytes 1-2)
    cpu: Annotated[int, Field(ge=0, le=16)] = 0  # 0.25 vCPU
    """Allocated vCPUs per bot: 0.25 vCPU (0) to 16 vCPU (6)"""

    memory: Annotated[int, Field(ge=0, le=120)] = 0  # 512 MiB
    """Total memory per bot (in GB)"""

    # NOTE: Configure # of workers based on cpu & memory settings

    # Runner configuration (Bytes 3-5)
    networks: Annotated[int, Field(ge=1, le=20)] = 1
    """Maximum number of concurrent network runners"""

    bots: Annotated[int, Field(ge=1, le=250)] = 1
    """Maximum number of concurrent bots running"""

    triggers: Annotated[int, Field(ge=50, le=1000, multiple_of=5)] = 50
    """Maximum number of task triggers across all running bots"""

    # Recorder configuration (Byte 6)
    storage: Annotated[int, Field(ge=0, le=250)] = 0  # 512 GB
    """Total task results and metrics parquet storage (in TB)"""

    # Cluster general configuration (Byte 7)
    secrets: Annotated[int, Field(ge=10, le=100)] = 10
    """Total managed secrets"""

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

    @field_validator("storage", mode="before")
    def parse_storage_value(cls, value: str | int) -> int:
        if not isinstance(value, str):
            return value

        storage, units = value.split(" ")
        if units.lower() == "gb":
            assert storage == "512"
            return 0

        assert units.lower() == "tb"
        return int(storage)

    @staticmethod
    def _decode_byte(value: int, byte: int) -> int:
        # NOTE: All configuration settings must be uint8 integer values when encoded
        return (value >> (8 * byte)) & (2**8 - 1)  # NOTE: max uint8

    @classmethod
    def decode(cls, value: int) -> "ClusterConfiguration":
        """Decode the configuration from 8 byte integer value"""
        if isinstance(value, ClusterConfiguration):
            return value  # TODO: Something weird with SQLModel

        # NOTE: Do not change the order of these, these are not forwards compatible
        return cls(
            version=cls._decode_byte(value, 0),
            cpu=cls._decode_byte(value, 1),
            memory=cls._decode_byte(value, 2),
            networks=cls._decode_byte(value, 3),
            bots=cls._decode_byte(value, 4),
            triggers=5 * cls._decode_byte(value, 5),
            storage=cls._decode_byte(value, 6),
            secrets=cls._decode_byte(value, 7),
        )

    @staticmethod
    def _encode_byte(value: int, byte: int) -> int:
        return value << (8 * byte)

    def encode(self) -> int:
        """Encode configuration as 8 byte integer value"""
        # NOTE: Do not change the order of these, these are not forwards compatible
        return (
            self._encode_byte(self.version, 0)
            + self._encode_byte(self.cpu, 1)
            + self._encode_byte(self.memory, 2)
            + self._encode_byte(self.networks, 3)
            + self._encode_byte(self.bots, 4)
            + self._encode_byte(self.triggers // 5, 5)
            + self._encode_byte(self.storage, 6)
            + self._encode_byte(self.secrets, 7)
        )


class ClusterTier(enum.IntEnum):
    """Suggestions for different tier configurations"""

    PERSONAL = ClusterConfiguration(
        cpu="0.25 vCPU",
        memory="512 MiB",
        networks=3,
        bots=5,
        triggers=50,
        storage="512 GB",
        secrets=10,
    ).encode()
    PROFESSIONAL = ClusterConfiguration(
        cpu="1 vCPU",
        memory="2 GB",
        networks=10,
        bots=20,
        triggers=400,
        storage="5 TB",
        secrets=25,
    ).encode()

    def configuration(self) -> ClusterConfiguration:
        return ClusterConfiguration.decode(int(self))


class ResourceStatus(enum.IntEnum):
    """
    Generic enum that represents that status of any associated resource or service.

    ```{note}
    Calling `str(...)` on this will produce a human-readable status for display.
    ```
    """

    CREATED = 0
    """Resource record created, but not provisioning yet (likely awaiting payment)"""

    # NOTE: `1` is reserved

    PROVISIONING = 2
    """Resource is provisioning infrastructure (on payment received)"""

    STARTUP = 3
    """Resource is being put into the RUNNING state"""

    RUNNING = 4
    """Resource is in good health (Resource itself should be reporting status now)"""

    # NOTE: `5` is reserved

    SHUTDOWN = 6
    """Resource is being put into the STOPPED state"""

    STOPPED = 7
    """Resource has stopped (due to errors, user action, or resource contraints)"""

    DEPROVISIONING = 8
    """User removal action or payment expiration event triggered"""

    REMOVED = 9
    """Infrastructure de-provisioning complete (Cannot change from this state)"""

    def __str__(self) -> str:
        return self.name.capitalize()


class ClusterInfo(BaseModel):
    # NOTE: Raw API object (gets exported)
    id: uuid.UUID  # NOTE: Keep this private, used as a temporary secret key for payment
    version: str | None  # NOTE: Unprovisioned clusters have no known version yet
    configuration: ClusterConfiguration | None = None  # NOTE: self-hosted clusters have no config

    name: str  # User-friendly display name
    slug: str  # Shorthand name, for CLI and URI usage

    created: datetime  # When the resource was first created
    status: ResourceStatus
    last_updated: datetime  # Last time the resource was changed (upgrade, provisioning, etc.)


class ClusterState(BaseModel):
    """
    Cluster Build Information and Configuration, direct from cluster control service
    """

    version: str = Field(alias="cluster_version")  # TODO: Rename in cluster
    configuration: ClusterConfiguration | None = None  # TODO: Add to cluster
    # TODO: Add other useful summary fields for frontend use


class ServiceHealth(BaseModel):
    healthy: bool


class ClusterHealth(BaseModel):
    ars: ServiceHealth = Field(exclude=True)  # TODO: Replace w/ cluster
    ccs: ServiceHealth = Field(exclude=True)  # TODO: Replace w/ cluster
    bots: dict[str, ServiceHealth] = {}

    @field_validator("bots", mode="before")  # TODO: Fix so this is default
    def convert_bot_health(cls, bots):
        return {b["instance_id"]: ServiceHealth.model_validate(b) for b in bots}

    @computed_field
    def cluster(self) -> ServiceHealth:
        return ServiceHealth(healthy=self.ars.healthy and self.ccs.healthy)


class VariableGroupInfo(BaseModel):
    id: uuid.UUID
    name: str
    revision: int
    variables: list[str]
    created: datetime


class EnvironmentVariable(BaseModel):
    name: str
    group_id: uuid.UUID
    group_revision: int


class BotTaskStatus(BaseModel):
    last_status: str
    exit_code: int | None
    reason: str | None
    started_at: datetime | None
    stop_code: str | None
    stopped_at: datetime | None
    stopped_reason: str | None


class BotHealth(BaseModel):
    bot_id: uuid.UUID
    task_status: BotTaskStatus | None
    healthy: bool


class BotInfo(BaseModel):
    id: uuid.UUID  # TODO: Change `.instance_id` field to `id: UUID`
    name: str
    created: datetime

    image: str
    network: str
    account: str | None
    revision: int

    environment: list[EnvironmentVariable] = []
