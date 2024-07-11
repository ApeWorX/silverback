from functools import cache
from typing import ClassVar

import httpx

from silverback.version import version

from .types import BotInfo, ClusterConfiguration, ClusterInfo, WorkspaceInfo

DEFAULT_HEADERS = {"User-Agent": f"Silverback SDK/{version}"}


def handle_error_with_response(response: httpx.Response):
    if 400 <= response.status_code < 500:
        message = response.text
        try:
            message = response.json().get("detail", response.text)
        except Exception:
            pass

        raise RuntimeError(message)

    response.raise_for_status()

    assert response.status_code < 300, "Should follow redirects, so not sure what the issue is"


class ClusterClient(httpx.Client):
    def __init__(self, *args, **kwargs):
        kwargs["headers"] = {**kwargs.get("headers", {}), **DEFAULT_HEADERS}
        super().__init__(*args, **kwargs)

    def send(self, request, *args, **kwargs):
        try:
            return super().send(request, *args, **kwargs)

        except httpx.ConnectError as e:
            raise ValueError(f"{e} '{request.url}'") from e

    @property
    @cache
    def openapi_schema(self) -> dict:
        return self.get("/openapi.json").json()

    @property
    def status(self) -> str:
        # NOTE: Just return full response directly to avoid errors
        return self.get("/").text

    @property
    def bots(self) -> dict[str, BotInfo]:
        # TODO: Actually connect to cluster and display options
        return {}


class Workspace(WorkspaceInfo):
    # NOTE: Client used only for this SDK
    # NOTE: DI happens in `PlatformClient.client`
    client: ClassVar[httpx.Client]

    def __hash__(self) -> int:
        return int(self.id)

    def get_cluster_client(self, cluster_name: str) -> ClusterClient:
        if not (cluster := self.clusters.get(cluster_name)):
            raise ValueError(f"Unknown cluster '{cluster_name}' in workspace '{self.name}'.")

        return ClusterClient(
            base_url=f"{self.client.base_url}/{self.slug}/{cluster.slug}",
            cookies=self.client.cookies,  # NOTE: pass along platform cookies for proxy auth
        )

    @property
    @cache
    def clusters(self) -> dict[str, ClusterInfo]:
        response = self.client.get("/clusters", params=dict(org=str(self.id)))
        handle_error_with_response(response)
        clusters = response.json()
        # TODO: Support paging
        return {cluster.slug: cluster for cluster in map(ClusterInfo.model_validate, clusters)}

    def create_cluster(
        self,
        cluster_slug: str = "",
        cluster_name: str = "",
        configuration: ClusterConfiguration = ClusterConfiguration(),
    ) -> ClusterInfo:
        body: dict = dict(configuration=configuration.model_dump())

        if cluster_slug:
            body["slug"] = cluster_slug

        if cluster_name:
            body["name"] = cluster_name

        response = self.client.post(
            "/clusters/",
            params=dict(org=str(self.id)),
            json=body,
        )

        handle_error_with_response(response)
        new_cluster = ClusterInfo.model_validate_json(response.text)
        self.clusters.update({new_cluster.slug: new_cluster})  # NOTE: Update cache
        return new_cluster


class PlatformClient(httpx.Client):
    def __init__(self, *args, **kwargs):
        if "follow_redirects" not in kwargs:
            kwargs["follow_redirects"] = True

        kwargs["headers"] = {**kwargs.get("headers", {}), **DEFAULT_HEADERS}
        super().__init__(*args, **kwargs)

        # DI for other client classes
        Workspace.client = self  # Connect to platform client

    def send(self, request, *args, **kwargs):
        try:
            return super().send(request, *args, **kwargs)

        except httpx.ConnectError as e:
            raise ValueError(f"{e} '{request.url}'") from e

    def get_cluster_client(self, workspace_name: str, cluster_name: str) -> ClusterClient:
        if not (workspace := self.workspaces.get(workspace_name)):
            raise ValueError(f"Unknown workspace '{workspace_name}'.")

        return workspace.get_cluster_client(cluster_name)

    @property
    @cache
    def workspaces(self) -> dict[str, Workspace]:
        response = self.get("/organizations")
        handle_error_with_response(response)
        workspaces = response.json()
        # TODO: Support paging
        return {
            workspace.slug: workspace for workspace in map(Workspace.model_validate, workspaces)
        }

    def create_workspace(
        self,
        workspace_slug: str = "",
        workspace_name: str = "",
    ) -> Workspace:
        response = self.post(
            "/organizations",
            json=dict(slug=workspace_slug, name=workspace_name),
        )
        handle_error_with_response(response)
        new_workspace = Workspace.model_validate_json(response.text)
        self.workspaces.update({new_workspace.slug: new_workspace})  # NOTE: Update cache
        return new_workspace


Client = PlatformClient | ClusterClient
