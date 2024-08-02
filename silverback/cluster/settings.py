from pathlib import Path

import tomlkit
from pydantic import BaseModel, Field, ValidationError, model_validator
from typing_extensions import Self

PROFILE_PATH = Path.home() / ".silverback" / "profile.toml"
DEFAULT_PROFILE = "default"


class AuthenticationConfig(BaseModel):
    """Authentication host configuration information (~/.silverback/profile.toml)"""

    host: str = "https://account.apeworx.io"
    client_id: str = Field(default="lcylrp34lnggGO-E-KKlMJgvAI4Q2Jhf6U2G6CB5uMg", alias="client-id")


class BaseProfile(BaseModel):
    """Profile information (~/.silverback/profile.toml)"""

    host: str


class ClusterProfile(BaseProfile):
    api_key: str = Field(alias="api-key")  # direct access to a cluster


class PlatformProfile(BaseProfile):
    auth: str  # key of `AuthenticationConfig` in authentication section
    default_workspace: str = Field(alias="default-workspace", default="")
    default_cluster: dict[str, str] = Field(alias="default-cluster", default_factory=dict)


class ProfileSettings(BaseModel):
    """Configuration settings for working with Bot Clusters and the Silverback Platform"""

    auth: dict[str, AuthenticationConfig]
    profile: dict[str, PlatformProfile | ClusterProfile]
    default_profile: str = Field(default=DEFAULT_PROFILE, alias="default-profile")

    @model_validator(mode="after")
    def ensure_auth_exists_for_profile(self) -> Self:
        for profile_name, profile in self.profile.items():
            if isinstance(profile, PlatformProfile) and profile.auth not in self.auth:
                auth_names = "', '".join(self.auth)
                raise ValidationError(
                    f"Key `profile.'{profile_name}'.auth` must be one of '{auth_names}'."
                )

        return self

    @classmethod
    def from_config_file(cls) -> Self:
        # TODO: Figure out why `BaseSettings` doesn't work well (probably uses tomlkit)
        settings_dict: dict  # NOTE: So mypy knows it's not redefined

        if PROFILE_PATH.exists():
            # NOTE: cast to dict because tomlkit has a bug in it that mutates dicts
            settings_dict = dict(tomlkit.loads(PROFILE_PATH.read_text()))

        else:  # Write the defaults to disk for next time
            settings_dict = dict(
                auth={
                    DEFAULT_PROFILE: AuthenticationConfig().model_dump(),
                },
                profile={
                    DEFAULT_PROFILE: PlatformProfile(
                        auth=DEFAULT_PROFILE,
                        host="https://silverback.apeworx.io",
                    ).model_dump()
                },
            )
            PROFILE_PATH.parent.mkdir(exist_ok=True)
            PROFILE_PATH.write_text(tomlkit.dumps(settings_dict))

        return cls.model_validate(settings_dict)
