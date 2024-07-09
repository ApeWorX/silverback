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

from .types import BotInfo, ClusterInfo, WorkspaceInfo

CREDENTIALS_FOLDER = Path.home() / ".silverback"
CREDENTIALS_FOLDER.mkdir(exist_ok=True)
DEFAULT_PROFILE = "production"


class ClusterClient(ClusterInfo):
    workspace: WorkspaceInfo
    # NOTE: Client used only for this SDK
    _client: ClassVar[httpx.Client]

    def __hash__(self) -> int:
        return int(self.id)

    @property
    @cache
    def client(self) -> httpx.Client:
        assert self._client, "Forgot to link platform client"
        # NOTE: DI happens in `PlatformClient.client`
        return httpx.Client(
            base_url=f"{self._client.base_url}/{self.workspace.slug}/{self.slug}",
            cookies=self._client.cookies,
            headers=self._client.headers,
        )

    @property
    @cache
    def openapi_schema(self) -> dict:
        return self.client.get("/openapi.json").json()

    @property
    def bots(self) -> dict[str, BotInfo]:
        # TODO: Actually connect to cluster and display options
        return {}


class WorkspaceClient(WorkspaceInfo):
    # NOTE: Client used only for this SDK
    # NOTE: DI happens in `PlatformClient.client`
    client: ClassVar[httpx.Client]

    def __hash__(self) -> int:
        return int(self.id)

    def parse_cluster(self, data: dict) -> ClusterClient:
        return ClusterClient.model_validate(dict(**data, workspace=self))

    @property
    @cache
    def clusters(self) -> dict[str, ClusterClient]:
        response = self.client.get("/clusters", params=dict(org=str(self.id)))
        response.raise_for_status()
        clusters = response.json()
        # TODO: Support paging
        return {cluster.slug: cluster for cluster in map(self.parse_cluster, clusters)}

    def create_cluster(
        self,
        cluster_slug: str = "",
        cluster_name: str = "",
        configuration: ClusterConfiguration = ClusterConfiguration(),
    ) -> ClusterClient:
        body: dict = dict(configuration=configuration.model_dump())

        if cluster_slug:
            body["slug"] = cluster_slug

        if cluster_name:
            body["name"] = cluster_name

        if (
            response := self.client.post(
                "/clusters/",
                params=dict(org=str(self.id)),
                json=body,
            )
        ).status_code >= 400:
            message = response.text
            try:
                message = response.json().get("detail", response.text)
            except Exception:
                pass

            raise RuntimeError(message)

        new_cluster = ClusterClient.model_validate_json(response.text)
        self.clusters.update({new_cluster.slug: new_cluster})  # NOTE: Update cache
        return new_cluster


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

        # DI for other client classes
        WorkspaceClient.client = client  # Connect to client
        ClusterClient._client = client  # Connect to client
        return client

    @property
    def userinfo(self) -> FiefUserInfo:
        return self.auth.current_user()

    @property
    def access_token_info(self) -> FiefAccessTokenInfo:
        return self.auth.access_token_info()

    @property
    @cache
    def workspaces(self) -> dict[str, WorkspaceClient]:
        response = self.client.get("/organizations")
        response.raise_for_status()
        workspaces = response.json()
        # TODO: Support paging
        return {
            workspace.slug: workspace
            for workspace in map(WorkspaceClient.model_validate, workspaces)
        }
