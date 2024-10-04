from functools import cache
from typing import ClassVar, Literal

import httpx

from silverback.version import version

from .types import (
    BotHealth,
    BotInfo,
    ClusterHealth,
    ClusterInfo,
    ClusterState,
    VariableGroupInfo,
    WorkspaceInfo,
)

DEFAULT_HEADERS = {"User-Agent": f"Silverback SDK/{version}"}


def handle_error_with_response(response: httpx.Response):
    if 400 <= response.status_code < 500:
        message = response.text

        try:
            message = response.json()
        except Exception:
            pass

        if isinstance(message, dict):
            if detail := message.get("detail"):
                if isinstance(detail, list):

                    def render_error(error: dict):
                        location = ".".join(error["loc"])
                        return f"- {location}: '{error['msg']}'"

                    message = "Multiple validation errors found:\n" + "\n".join(
                        map(render_error, detail)
                    )

                else:
                    message = detail

            else:
                message = response.text

        raise RuntimeError(message)

    response.raise_for_status()

    assert response.status_code < 300, "Should follow redirects, so not sure what the issue is"


class VariableGroup(VariableGroupInfo):
    # NOTE: Client used only for this SDK
    # NOTE: DI happens in `ClusterClient.__init__`
    cluster: ClassVar["ClusterClient"]

    def __hash__(self) -> int:
        return int(self.id)

    def update(
        self, name: str | None = None, variables: dict[str, str | None] | None = None
    ) -> "VariableGroup":
        if name is not None:
            # Update metadata
            response = self.cluster.put(f"/variables/{self.id}", json=dict(name=name))
            handle_error_with_response(response)

        if variables is not None:
            # Create a new revision
            response = self.cluster.post(f"/variables/{self.id}", json=dict(variables=variables))
            handle_error_with_response(response)
            return VariableGroup.model_validate(response.json())

        return self

    def get_revision(self, revision: int | Literal["latest"] = "latest") -> VariableGroupInfo:
        # TODO: Add `/latest` revision route
        if revision == "latest":
            revision = ""  # type: ignore[assignment]

        response = self.cluster.get(f"/variables/{self.id}/{revision}")
        handle_error_with_response(response)
        return VariableGroupInfo.model_validate(response.json())

    def remove(self):
        response = self.cluster.delete(f"/variables/{self.id}")
        handle_error_with_response(response)


class Bot(BotInfo):
    # NOTE: Client used only for this SDK
    # NOTE: DI happens in `ClusterClient.__init__`
    cluster: ClassVar["ClusterClient"]

    def update(
        self,
        name: str | None = None,
        image: str | None = None,
        network: str | None = None,
        account: str | None = None,
        environment: list[VariableGroupInfo] | None = None,
    ) -> "Bot":
        form: dict = dict(
            name=name,
            account=account,
            image=image,
            network=network,
        )

        if environment:
            form["environment"] = [
                dict(id=str(env.id), revision=env.revision) for env in environment
            ]

        response = self.cluster.put(f"/bots/{self.id}", json=form)
        handle_error_with_response(response)
        return Bot.model_validate(response.json())

    @property
    def health(self) -> BotHealth:
        response = self.cluster.get("/health")  # TODO: Migrate this endpoint
        # response = self.cluster.get(f"/bots/{self.id}/health")
        handle_error_with_response(response)
        raw_health = next(bot for bot in response.json()["bots"] if bot["bot_id"] == str(self.id))
        return BotHealth.model_validate(raw_health)  # response.json())  TODO: Migrate this endpoint

    def stop(self):
        response = self.cluster.post(f"/bots/{self.id}/stop")
        handle_error_with_response(response)

    def start(self):
        # response = self.cluster.post(f"/bots/{self.id}/start") TODO: Add `/start`
        # NOTE: Currently, a noop PUT request will trigger a start
        response = self.cluster.put(f"/bots/{self.id}", json=dict(name=self.name))
        handle_error_with_response(response)

    @property
    def errors(self) -> list[str]:
        response = self.cluster.get(f"/bots/{self.id}/errors")
        handle_error_with_response(response)
        return response.json()

    @property
    def logs(self) -> list[str]:
        response = self.cluster.get(f"/bots/{self.id}/logs")
        handle_error_with_response(response)
        return response.json()

    def remove(self):
        response = self.cluster.delete(f"/bots/{self.id}")
        handle_error_with_response(response)


class ClusterClient(httpx.Client):
    def __init__(self, *args, **kwargs):
        kwargs["headers"] = {**kwargs.get("headers", {}), **DEFAULT_HEADERS}
        if "follow_redirects" not in kwargs:
            kwargs["follow_redirects"] = True

        super().__init__(*args, **kwargs)

        # DI for other client classes
        VariableGroup.cluster = self  # Connect to cluster client
        Bot.cluster = self  # Connect to cluster client

    def send(self, request, *args, **kwargs):
        try:
            return super().send(request, *args, **kwargs)

        except httpx.ConnectError as e:
            raise ValueError(f"{e} '{request.url}'") from e

    @property
    @cache
    def openapi_schema(self) -> dict:
        response = self.get("/openapi.json")
        handle_error_with_response(response)
        return response.json()

    @property
    def version(self) -> str:
        # NOTE: Does not call routes
        return self.openapi_schema["info"]["version"]

    @property
    def state(self) -> ClusterState:
        response = self.get("/")
        handle_error_with_response(response)
        return ClusterState.model_validate(response.json())

    @property
    def health(self) -> ClusterHealth:
        response = self.get("/health")
        handle_error_with_response(response)
        return ClusterHealth.model_validate(response.json())

    @property
    def variable_groups(self) -> dict[str, VariableGroup]:
        response = self.get("/variables")
        handle_error_with_response(response)
        return {vg.name: vg for vg in map(VariableGroup.model_validate, response.json())}

    def new_variable_group(self, name: str, variables: dict[str, str]) -> VariableGroup:
        response = self.post("/variables", json=dict(name=name, variables=variables))
        handle_error_with_response(response)
        return VariableGroup.model_validate(response.json())

    @property
    def bots(self) -> dict[str, Bot]:
        response = self.get("/bots")
        handle_error_with_response(response)
        return {bot.name: bot for bot in map(Bot.model_validate, response.json())}

    def new_bot(
        self,
        name: str,
        image: str,
        network: str,
        account: str | None = None,
        environment: list[VariableGroupInfo] | None = None,
    ) -> Bot:
        form: dict = dict(
            name=name,
            image=image,
            network=network,
            account=account,
        )

        if environment is not None:
            form["environment"] = [
                dict(id=str(env.id), revision=env.revision) for env in environment
            ]

        response = self.post("/bots", json=form)
        handle_error_with_response(response)
        return Bot.model_validate(response.json())


class Workspace(WorkspaceInfo):
    # NOTE: Client used only for this SDK
    # NOTE: DI happens in `PlatformClient.client`
    client: ClassVar[httpx.Client]

    @property
    @cache
    def owner(self) -> str:
        response = self.client.get(f"/users/{self.owner_id}")
        handle_error_with_response(response)
        return response.json().get("username")

    def build_display_fields(self) -> dict[str, str]:
        return dict(
            # `.id` is internal
            name=self.name,
            # `.slug` is index
            # `.owner_id` is UUID, use for client lookup instead
            owner=self.owner,
        )

    def __hash__(self) -> int:
        return int(self.id)

    def get_cluster_client(self, cluster_name: str) -> ClusterClient:
        if not (cluster := self.clusters.get(cluster_name)):
            raise ValueError(f"Unknown cluster '{cluster_name}' in workspace '{self.name}'.")

        return ClusterClient(
            base_url=f"{self.client.base_url}/c/{self.slug}/{cluster.slug}",
            cookies=self.client.cookies,  # NOTE: pass along platform cookies for proxy auth
        )

    @property
    @cache
    def clusters(self) -> dict[str, ClusterInfo]:
        response = self.client.get("/clusters", params=dict(workspace=str(self.id)))
        handle_error_with_response(response)
        clusters = response.json()
        # TODO: Support paging
        return {cluster.slug: cluster for cluster in map(ClusterInfo.model_validate, clusters)}

    def create_cluster(
        self,
        cluster_slug: str | None = None,
        cluster_name: str | None = None,
    ) -> ClusterInfo:
        response = self.client.post(
            "/clusters/",
            params=dict(workspace=str(self.id)),
            json=dict(name=cluster_name, slug=cluster_slug),
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
        response = self.get("/workspaces")
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
            "/workspaces",
            json=dict(slug=workspace_slug, name=workspace_name),
        )
        handle_error_with_response(response)
        new_workspace = Workspace.model_validate_json(response.text)
        self.workspaces.update({new_workspace.slug: new_workspace})  # NOTE: Update cache
        return new_workspace
