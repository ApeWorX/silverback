import os
from functools import cache
from pathlib import Path
from typing import ClassVar

import httpx
import tomlkit
from fief_client import Fief, FiefAccessTokenInfo, FiefUserInfo
from fief_client.integrations.cli import FiefAuth

from silverback.platform.types import ClusterConfiguration
from silverback.version import version

from .types import BotInfo, ClusterInfo

CREDENTIALS_FOLDER = Path.home() / ".silverback"
CREDENTIALS_FOLDER.mkdir(exist_ok=True)
DEFAULT_PROFILE = "production"


class ClusterClient(ClusterInfo):
    # NOTE: Client used only for this SDK
    platform_client: ClassVar[httpx.Client | None] = None

    def __hash__(self) -> int:
        return int(self.id)

    @property
    @cache
    def client(self) -> httpx.Client:
        assert self.platform_client, "Forgot to link platform client"
        # NOTE: DI happens in `PlatformClient.client`
        return httpx.Client(
            base_url=f"{self.platform_client.base_url}/clusters/{self.name}",
            cookies=self.platform_client.cookies,
            headers=self.platform_client.headers,
        )

    @property
    @cache
    def openapi_schema(self) -> dict:
        return self.client.get("/openapi.json").json()

    @property
    def bots(self) -> dict[str, BotInfo]:
        # TODO: Actually connect to cluster and display options
        return {}


class PlatformClient:
    def __init__(self, profile_name: str = DEFAULT_PROFILE):
        if not (profile_toml := (CREDENTIALS_FOLDER / "profile.toml")).exists():
            if profile_name != DEFAULT_PROFILE:
                raise RuntimeError(f"create '{profile_toml}' to add custom profile")

            # Cache this for later
            profile_toml.write_text(
                tomlkit.dumps(
                    {
                        DEFAULT_PROFILE: {
                            "auth-domain": "https://account.apeworx.io",
                            "host-url": "https://silverback.apeworx.io",
                            "client-id": "lcylrp34lnggGO-E-KKlMJgvAI4Q2Jhf6U2G6CB5uMg",
                        }
                    }
                )
            )

        if not (profile := tomlkit.loads(profile_toml.read_text()).get(profile_name)):
            raise RuntimeError(f"Unknown profile {profile_name}")

        fief = Fief(profile["auth-domain"], profile["client-id"])
        self.auth = FiefAuth(fief, str(CREDENTIALS_FOLDER / f"{profile_name}.json"))

        # NOTE: Use `SILVERBACK_PLATFORM_HOST=http://127.0.0.1:8000` for local testing
        self.base_url = os.environ.get("SILVERBACK_PLATFORM_HOST") or profile["host-url"]

    @property
    @cache
    def client(self) -> httpx.Client:
        client = httpx.Client(
            base_url=self.base_url,
            # NOTE: Raises `FiefAuthNotAuthenticatedError` if access token not available
            cookies={"session": self.access_token_info["access_token"]},
            headers={"User-Agent": f"Silverback SDK/{version}"},
            follow_redirects=True,
        )

        # Detect connection fault early
        try:
            self.openapi = client.get("/openapi.json").json()
        except httpx.ConnectError:
            raise RuntimeError(f"No Platform API Host detected at '{self.base_url}'.")
        except Exception:
            raise RuntimeError(f"Error with API Host at '{self.base_url}'.")

        # DI for `ClusterClient`
        ClusterClient.platform_client = client  # Connect to client
        return client

    @property
    def userinfo(self) -> FiefUserInfo:
        return self.auth.current_user()

    @property
    def access_token_info(self) -> FiefAccessTokenInfo:
        return self.auth.access_token_info()

    @property
    @cache
    def clusters(self) -> dict[str, ClusterClient]:
        response = self.client.get("/clusters/")
        response.raise_for_status()
        clusters = response.json()
        # TODO: Support paging
        return {cluster.name: cluster for cluster in map(ClusterClient.parse_obj, clusters)}

    def create_cluster(
        self,
        cluster_name: str = "",
        configuration: ClusterConfiguration = ClusterConfiguration(),
    ) -> ClusterClient:
        if (
            response := self.client.post(
                "/clusters/",
                params=dict(name=cluster_name),
                json=configuration.model_dump(),
            )
        ).status_code >= 400:
            message = response.text
            try:
                message = response.json().get("detail", response.text)
            except Exception:
                pass

            raise RuntimeError(message)

        return ClusterClient.parse_raw(response.text)
