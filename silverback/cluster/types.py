from __future__ import annotations

import enum
import re
import uuid
from datetime import datetime
from typing import Annotated, Any, ClassVar

from ape.logging import CLICK_STYLE_KWARGS, LogLevel
from ape.types import AddressType, HexBytes
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.hmac import HMAC, hashes
from eth_utils import to_bytes, to_int
from pydantic import BaseModel, Field, computed_field, field_validator
from typing_extensions import Self


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

    version: int = 1
    """Version of this configuration (used for encoding/decoding)"""
    # NOTE: Update this to revise new models for every configuration change

    # Cluster-wide limits on maximum usage (Bytes 1-2)
    cpu: Annotated[int, Field(ge=1, le=64)] = 1  # defaults to 1 vCPU
    """Max vCPUs shared by all bots in cluster"""

    memory: Annotated[int, Field(ge=1, le=128)] = 1
    """Max memory (in GiB) shared by all bots in cluster"""

    # NOTE: # of workers in each bot configured based on above cpu & memory settings

    # Runner configuration (Bytes 3-4)
    networks: Annotated[int, Field(ge=1, le=20)] = 1
    """Maximum number of concurrent networks all bots can use"""

    bots: Annotated[int, Field(ge=1, le=250)] = 1
    """Maximum number of guaranteed concurrently running bots"""
    # NOTE: Some amount of spare capacity allowed over this limit, depending on cluster load

    # NOTE: Byte 5 is reserved for future use (non-breaking)

    # Recorder configuration (Bytes 6-7)
    bandwidth: Annotated[int, Field(ge=1, le=250)] = 1  # 1 KiB/sec (~2.5 GiB/month)
    """Rate at which data should be emitted by cluster (in KiB/sec)"""
    # NOTE: This rate will serve as a throttling threshold on results processing / data streaming

    duration: Annotated[int, Field(ge=1, le=120)] = 1
    """Time to keep data recording duration (in months)"""
    # NOTE: The storage space alloted for your recordings will be `bandwidth x duration`.
    #       If the storage space is exceeded, it will be aggressively pruned to maintain that size.
    #       We will also prune data with duration past this point, if there is unused space.

    @field_validator("cpu", "memory", "bandwidth", mode="before")
    def parse_units(cls, value: str | int) -> int:
        if not isinstance(value, str):
            return value

        amount, _ = value.split(" ")
        return int(amount)

    def settings_display_dict(self) -> dict:
        return dict(
            version=self.version,
            runner=dict(
                bots=self.bots,
                networks=self.networks,
            ),
            cluster=dict(
                cpu=f"{self.cpu} vCPU",
                memory=f"{self.memory} GiB",
            ),
            recorder=dict(
                bandwidth=f"{self.bandwidth} KiB/sec",
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

    def get_product_code(self, owner: AddressType, cluster_id: uuid.UUID) -> HexBytes:
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
        self, owner: AddressType, signature: bytes, cluster_id: uuid.UUID
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

    STANDARD = ClusterConfiguration(
        cpu="1 vCPU",
        memory="2 GiB",
        networks=3,
        bots=5,
        bandwidth="1 KiB/sec",  # 2.47 GB/mo
        duration=3,  # months
    ).encode()
    PREMIUM = ClusterConfiguration(
        cpu="4 vCPU",
        memory="8 GiB",
        networks=10,
        bots=20,
        bandwidth="5 KiB/sec",  # 12.36 GB/mo
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

    expiration: datetime | None = None  # NOTE: Self-hosted clusters have no expiration
    stream_id: uuid.UUID | None = None  # NOTE: If there is an ApePay payment stream for this

    created: datetime  # When the resource was first created
    status: ResourceStatus
    last_updated: datetime  # Last time the resource was changed (upgrade, provisioning, etc.)


class ServiceHealth(BaseModel):
    healthy: bool


class ClusterHealth(BaseModel):
    # NOTE: network => healthy
    networks: dict[str, ServiceHealth] = Field(default_factory=dict)
    bots: dict[str, ServiceHealth] = Field(default_factory=dict)

    @field_validator("bots", mode="before")  # TODO: Fix so this is default
    def convert_bot_health(cls, bots):
        return {k: ServiceHealth.model_validate(b) for k, b in bots.items()}

    @computed_field
    def cluster(self) -> ServiceHealth:
        return ServiceHealth(
            healthy=all(n.healthy for n in self.networks.values())
            and all(b.healthy for b in self.bots.values())
        )


class RegistryCredentialsInfo(BaseModel):
    id: str
    name: str
    hostname: str
    created: datetime
    updated: datetime


class VariableGroupInfo(BaseModel):
    id: uuid.UUID
    name: str
    variables: list[str]
    created: datetime


class BotTaskStatus(BaseModel):
    last_status: str
    exit_code: int | None
    reason: str | None
    started_at: datetime | None
    stop_code: str | None
    stopped_at: datetime | None
    stopped_reason: str | None


class BotInfo(BaseModel):
    id: uuid.UUID  # TODO: Change `.instance_id` field to `id: UUID`
    name: str
    created: datetime

    image: str
    credential_name: str | None
    ecosystem: str
    network: str
    provider: str
    account: str | None
    environment: list[str]


class BotLogEntry(BaseModel):
    LOG_PATTERN: ClassVar[re.Pattern] = re.compile(
        r"""^
    (?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)\s
    (?:
        (?P<level>DEBUG:\s\s\s|INFO:\s\s\s\s|SUCCESS:\s|WARNING:\s|ERROR:\s\s\s)\s
    )?
    (?P<message>.*)$""",
        re.VERBOSE,
    )
    # NOTE: Add offset (18+8+2=28) to all newlines in message after the first
    LOGLINE_OFFSET: ClassVar[str] = "\n" + " " * 27 + "| "

    level: LogLevel | None = None
    timestamp: datetime | None = None
    message: str

    @classmethod
    def parse_line(cls, line: str) -> Self:
        # Typical line is like: `{timestamp} {str(log_level) + ':':<9} {message}`
        if not (match := cls.LOG_PATTERN.match(line)):
            return cls(message=line)

        if level := match.group("level"):
            level = LogLevel[level.strip()[:-1]]

        return cls(
            timestamp=match.group("timestamp"),
            level=level,
            message=match.group("message"),
        )

    def __str__(self) -> str:
        from click import style as click_style

        if self.level is not None:
            styles = CLICK_STYLE_KWARGS.get(self.level, {})
            level_str = click_style(f"{self.level.name:<8}", **styles)  # type: ignore[arg-type]
        else:
            level_str = ""

        if self.timestamp is not None:
            timestamp_str = click_style(f"{self.timestamp.astimezone():%x %X}", bold=True)
        else:
            timestamp_str = ""

        if "\n" in (message := self.message):
            message = self.LOGLINE_OFFSET.join(message.split("\n"))

        # NOTE: Max size of `LogLevel` is 8 chars
        # NOTE: Max size of normalized timestamp is 18 chars
        return f"{timestamp_str:<18} {level_str:<8} | {message}"
