import enum
import math
import uuid
from datetime import datetime
from typing import Annotated, Any

from ape.types import AddressType
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.hmac import HMAC, hashes
from eth_pydantic_types import Address, HexBytes
from eth_utils import to_bytes, to_int
from pydantic import BaseModel, Field, computed_field, field_validator


def normalize_bytes(val: bytes, length: int = 16) -> bytes:
    return val + b"\x00" * (length - len(val))


class WorkspaceInfo(BaseModel):
    id: uuid.UUID
    owner_id: uuid.UUID
    name: str
    slug: str
    created: datetime


class ClusterConfiguration(BaseModel):
    """Configuration of the cluster (represented as 16 byte value)"""

    # NOTE: This configuration must be encode-able to a uint64 value for db duration and on-chain
    #       processing through ApePay
    # NOTE: All defaults should be the minimal end of the scale, so that `__or__` works right

    # Version byte (Byte 0)
    # NOTE: Update this to revise new models for every configuration change
    version: int = 1

    # Bot Worker Configuration, priced per bot (Bytes 1-2)
    cpu: Annotated[int, Field(ge=0, le=6)] = 0  # defaults to 0.25 vCPU
    """Allocated vCPUs per bot:
    - 0.25 vCPU (0)
    - 0.50 vCPU (1)
    - 1.00 vCPU (2)
    - 2.00 vCPU (3)
    - 4.00 vCPU (4)
    - 8.00 vCPU (5)
    - 16.0 vCPU (6)"""

    memory: Annotated[int, Field(ge=0, le=120)] = 0  # defaults to 512 MiB
    """Total memory per bot (in GB, 0 means '512 MiB')"""

    # NOTE: # of workers configured based on cpu & memory settings

    # Runner configuration (Bytes 3-4)
    networks: Annotated[int, Field(ge=1, le=20)] = 1
    """Maximum number of concurrent network runners"""

    bots: Annotated[int, Field(ge=1, le=250)] = 1
    """Maximum number of concurrent running bots"""

    # NOTE: Byte 5 unused

    # Recorder configuration (Bytes 6-7)
    bandwidth: Annotated[int, Field(ge=0, le=250)] = 0  # 512 kB/sec
    """Rate at which data should be emitted by cluster (in MB/sec, 0 means '512 kB')"""
    # NOTE: This rate is only estimated average, and will serve as a throttling threshold

    duration: Annotated[int, Field(ge=1, le=120)] = 1
    """Time to keep data recording duration (in months)"""
    # NOTE: The storage space alloted for your recordings will be `bandwidth x duration`.
    #       If the storage space is exceeded, it will be aggressively pruned to maintain that size.
    #       We will also prune duration past that point less aggressively, if there is unused space.

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
        if units.lower() in ("mib", "mb"):
            assert mem == "512"
            return 0

        assert units.lower() == "gb"
        return int(mem)

    @field_validator("bandwidth", mode="before")
    def parse_bandwidth_value(cls, value: str | int) -> int:
        if not isinstance(value, str):
            return value

        bandwidth, units = value.split(" ")
        if units.lower() == "b/sec":
            assert bandwidth == "512"
            return 0

        assert units.lower() == "kb/sec"
        return int(bandwidth)

    def settings_display_dict(self) -> dict:
        return dict(
            version=self.version,
            runner=dict(
                networks=self.networks,
                bots=self.bots,
            ),
            bots=dict(
                cpu=f"{256 * 2**self.cpu / 1024} vCPU",
                memory=f"{self.memory} GB" if self.memory > 0 else "512 MiB",
            ),
            recorder=dict(
                bandwidth=f"{self.bandwidth} MB/sec" if self.bandwidth > 0 else "512 kB/sec",
                duration=f"{self.duration} months",
            ),
        )

    @staticmethod
    def _decode_byte(value: int, byte: int) -> int:
        # NOTE: All configuration settings must be uint8 integer values when encoded
        return (value >> (8 * byte)) & (2**8 - 1)  # NOTE: max uint8

    @classmethod
    def decode(cls, value: Any) -> "ClusterConfiguration":
        """Decode the configuration from 8 byte integer value"""
        if isinstance(value, ClusterConfiguration):
            return value  # TODO: Something weird with SQLModel

        elif isinstance(value, bytes):
            value = to_int(value)

        elif not isinstance(value, int):
            raise ValueError(f"Cannot decode type: '{type(value)}'")

        # NOTE: Do not change the order of these, these are not forwards compatible
        if (version := cls._decode_byte(value, 0)) == 1:
            return cls(
                version=version,
                cpu=cls._decode_byte(value, 1),
                memory=cls._decode_byte(value, 2),
                networks=cls._decode_byte(value, 3),
                bots=cls._decode_byte(value, 4),
                bandwidth=cls._decode_byte(value, 6),
                duration=cls._decode_byte(value, 7),
            )

        # NOTE: Update this to revise new models for every configuration change

        raise ValueError(f"Unsupported version: '{version}'")

    @staticmethod
    def _encode_byte(value: int, byte: int) -> int:
        return value << (8 * byte)

    def encode(self) -> int:
        """Encode configuration as 8 byte integer value"""
        # NOTE: Only need to encode the latest version, can change implementation below
        return (
            self._encode_byte(self.version, 0)
            + self._encode_byte(self.cpu, 1)
            + self._encode_byte(self.memory, 2)
            + self._encode_byte(self.networks, 3)
            + self._encode_byte(self.bots, 4)
            + self._encode_byte(self.bandwidth, 6)
            + self._encode_byte(self.duration, 7)
        )

    def get_product_code(self, owner: Address, cluster_id: uuid.UUID) -> HexBytes:
        # returns bytes32 product code `(sig || config)`
        config = normalize_bytes(to_bytes(self.encode()))

        # NOTE: MD5 is not recommended for general use, but is not considered insecure for HMAC use.
        #       However, our security property here is simple front-running protection to ensure
        #       only Workspace members can open a Stream to fund a Cluster (since `cluster_id` is a
        #       shared secret kept private between members of a Workspace when Cluster is created).
        #       Unless HMAC-MD5 can be shown insecure enough to recover the secret key in <5mins,
        #       this is probably good enough for now (and retains 16B size digest that fits with our
        #       encoded 16B configuration into a bytes32 val, to avoid memory expansion w/ DynArray)
        h = HMAC(cluster_id.bytes, hashes.MD5())
        h.update(normalize_bytes(to_bytes(hexstr=owner), length=20) + config)
        sig = normalize_bytes(h.finalize())  # 16 bytes

        return HexBytes(config + sig)

    def validate_product_code(
        self, owner: Address, signature: bytes, cluster_id: uuid.UUID
    ) -> bool:
        # NOTE: Put `cluster_id` last so it's easy to use with `functools.partial`
        config = normalize_bytes(to_bytes(self.encode()))

        h = HMAC(cluster_id.bytes, hashes.MD5())
        h.update(normalize_bytes(to_bytes(hexstr=owner), length=20) + config)

        try:
            h.verify(signature)
            return True

        except InvalidSignature:
            return False


class ClusterTier(enum.IntEnum):
    """Suggestions for different tier configurations"""

    PERSONAL = ClusterConfiguration(
        cpu="0.25 vCPU",
        memory="512 MiB",
        networks=3,
        bots=5,
        bandwidth="512 B/sec",  # 1.236 GB/mo
        duration=3,  # months
    ).encode()
    PROFESSIONAL = ClusterConfiguration(
        cpu="1 vCPU",
        memory="2 GB",
        networks=10,
        bots=20,
        bandwidth="5 kB/sec",  # 12.36 GB/mo
        duration=12,  # 1 year = ~148GB
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


class StreamInfo(BaseModel):
    chain_id: int
    manager: AddressType
    stream_id: int


class ClusterInfo(BaseModel):
    # NOTE: Raw API object (gets exported)
    id: uuid.UUID  # NOTE: Keep this private, used as a temporary secret key for payment
    version: str | None  # NOTE: Unprovisioned clusters have no known version yet
    configuration: ClusterConfiguration | None = None  # NOTE: self-hosted clusters have no config

    name: str  # User-friendly display name
    slug: str  # Shorthand name, for CLI and URI usage

    expiration: datetime | None = None  # NOTE: self-hosted clusters have no expiration

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
