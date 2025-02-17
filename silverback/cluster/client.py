from datetime import datetime
from functools import cache
from typing import ClassVar, Iterator

import httpx
from ape import Contract
from ape.contracts import ContractInstance
from ape.logging import LogLevel
from apepay import Stream, StreamManager
from pydantic import computed_field

from silverback.exceptions import ClientError
from silverback.version import version

from .types import (
    BotInfo,
    BotLogEntry,
    ClusterConfiguration,
    ClusterHealth,
    ClusterInfo,
    RegistryCredentialsInfo,
    ResourceStatus,
    ServiceHealth,
    StreamInfo,
    VariableGroupInfo,
    WorkspaceInfo,
)

DEFAULT_HEADERS = {"User-Agent": f"Silverback SDK/{version}"}


def handle_error_with_response(response: httpx.Response):
    if 400 <= response.status_code < 500:
        # NOTE: Must call `response.read()` for for streaming request
        # https://github.com/encode/httpx/discussions/1856#discussioncomment-1316674
        response.read()
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

        raise ClientError(message)

    response.raise_for_status()

    assert response.status_code < 300, "Should follow redirects, so not sure what the issue is"


class RegistryCredentials(RegistryCredentialsInfo):
    # NOTE: Client used only for this SDK
    # NOTE: DI happens in `ClusterClient.__init__`
    cluster: ClassVar["ClusterClient"]

    def __hash__(self) -> int:
        return int(self.id)

    def update(
        self,
        hostname: str | None = None,
        email: str | None = None,
        username: str | None = None,
        password: str | None = None,
    ) -> "RegistryCredentials":
        response = self.cluster.patch(
            f"/credentials/{self.name}",
            json=dict(hostname=hostname, email=email, username=username, password=password),
        )
        handle_error_with_response(response)
        return self

    def remove(self):
        response = self.cluster.delete(f"/credentials/{self.name}")
        handle_error_with_response(response)


class VariableGroup(VariableGroupInfo):
    # NOTE: Client used only for this SDK
    # NOTE: DI happens in `ClusterClient.__init__`
    cluster: ClassVar["ClusterClient"]

    def __hash__(self) -> int:
        return int(self.id)

    def update(self, **variables: str | None) -> "VariableGroup":
        response = self.cluster.patch(f"/vars/{self.id}", json=dict(variables=variables))
        handle_error_with_response(response)
        return VariableGroup.model_validate(response.json())

    def remove(self):
        response = self.cluster.delete(f"/vars/{self.id}")
        handle_error_with_response(response)


class Bot(BotInfo):
    # NOTE: Client used only for this SDK
    # NOTE: DI happens in `ClusterClient.__init__`
    cluster: ClassVar["ClusterClient"]

    @property
    def vargroups(self) -> list[VariableGroupInfo]:
        vargroups = self.cluster.variable_groups
        return [vargroups[vg_name] for vg_name in self.environment if vg_name in vargroups]

    def update(
        self,
        name: str | None = None,
        image: str | None = None,
        credential_name: str | None = "<no-change>",
        ecosystem: str | None = None,
        network: str | None = None,
        provider: str | None = None,
        account: str | None = "<no-change>",
        environment: list[str] | None = None,
    ) -> "Bot":
        form: dict = dict(
            name=name,
            image=image,
            credential_name=credential_name,
            ecosystem=ecosystem,
            network=network,
            provider=provider,
            account=account,
            environment=environment,
        )

        response = self.cluster.patch(
            f"/bots/{self.id}",
            json=form,
            # NOTE: Sometimes this command takes a little longer
            timeout=10,
        )
        handle_error_with_response(response)
        return Bot.model_validate(response.json())

    @property
    def status(self) -> ResourceStatus:
        response = self.cluster.get(f"/bots/{self.id}/status")
        handle_error_with_response(response)
        return ResourceStatus(response.json())

    @property
    def is_healthy(self) -> bool:
        response = self.cluster.get(f"/bots/{self.id}/health")
        handle_error_with_response(response)
        return ServiceHealth.model_validate(response.json()).healthy

    def stop(self):
        response = self.cluster.post(
            f"/bots/{self.id}/stop",
            # NOTE: Sometimes this command takes a little longer
            timeout=10,
        )
        handle_error_with_response(response)

    def start(self):
        response = self.cluster.post(
            f"/bots/{self.id}/start",
            # NOTE: Sometimes this command takes a little longer
            timeout=10,
        )
        handle_error_with_response(response)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def credential(self) -> RegistryCredentials | None:
        if self.credential_name:
            for v in self.cluster.credentials.values():
                if v.id == self.credential_name:
                    return v
        return None

    @property
    def errors(self) -> list[str]:
        response = self.cluster.get(f"/bots/{self.id}/errors")
        handle_error_with_response(response)
        return response.json()

    def get_logs(
        self,
        log_level: LogLevel = LogLevel.INFO,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        follow: bool = False,
    ) -> Iterator[BotLogEntry]:
        query: dict = dict(log_level=log_level.name, follow=follow)

        if start_time:
            query["start_time"] = start_time.isoformat()

        if end_time:
            query["end_time"] = end_time.isoformat()

        request = self.cluster.build_request(
            "GET",
            f"/bots/{self.id}/logs",
            params=query,
            # NOTE: Sometimes this command takes a little longer
            timeout=10,
        )
        response = self.cluster.send(request, stream=True)
        handle_error_with_response(response)
        yield from map(BotLogEntry.model_validate_json, response.iter_lines())

    @property
    def logs(self) -> list[BotLogEntry]:
        return list(self.get_logs())

    def remove(self):
        response = self.cluster.delete(
            f"/bots/{self.id}",
            # NOTE: Sometimes this command takes a little longer
            timeout=10,
        )
        handle_error_with_response(response)


class ClusterClient(httpx.Client):
    def __init__(self, *args, **kwargs):
        kwargs["headers"] = {**kwargs.get("headers", {}), **DEFAULT_HEADERS}
        if "follow_redirects" not in kwargs:
            kwargs["follow_redirects"] = True

        super().__init__(*args, **kwargs)

        # DI for other client classes
        RegistryCredentials.cluster = self  # Connect to cluster client
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
    def configuration(self) -> ClusterConfiguration | None:
        return self.openapi_schema["info"].get("x-config")

    @property
    def health(self) -> ClusterHealth:
        response = self.get("/health")
        handle_error_with_response(response)
        return ClusterHealth.model_validate(response.json())

    @property
    def credentials(self) -> dict[str, RegistryCredentials]:
        response = self.get("/credentials")
        handle_error_with_response(response)
        return {
            creds.name: creds for creds in map(RegistryCredentials.model_validate, response.json())
        }

    def new_credentials(
        self, name: str, hostname: str, email: str, username: str, password: str
    ) -> RegistryCredentials:
        response = self.post(
            "/credentials",
            json=dict(
                name=name,
                hostname=hostname,
                email=email,
                username=username,
                password=password,
            ),
        )
        handle_error_with_response(response)
        return RegistryCredentials.model_validate(response.json())

    @property
    def variable_groups(self) -> dict[str, VariableGroup]:
        response = self.get("/vars")
        handle_error_with_response(response)
        return {vg.name: vg for vg in map(VariableGroup.model_validate, response.json())}

    def new_variable_group(self, name: str, variables: dict[str, str]) -> VariableGroup:
        response = self.post("/vars", params={"name": name}, json=variables)
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
        ecosystem: str,
        network: str,
        provider: str,
        account: str | None = None,
        environment: list[str] | None = None,
        credential_name: str | None = None,
    ) -> Bot:
        form: dict = dict(
            name=name,
            image=image,
            ecosystem=ecosystem,
            network=network,
            provider=provider,
            account=account,
            environment=environment or [],
            credential_name=credential_name,
        )

        response = self.post(
            "/bots",
            json=form,
            # NOTE: Sometimes this command takes a little longer
            timeout=10,
        )
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
            data=dict(name=cluster_name, slug=cluster_slug),
        )

        handle_error_with_response(response)
        new_cluster = ClusterInfo.model_validate_json(response.text)
        self.clusters.update({new_cluster.slug: new_cluster})  # NOTE: Update cache
        return new_cluster

    def update_cluster(
        self,
        cluster_id: str,
        name: str | None = None,
        slug: str | None = None,
    ) -> ClusterInfo:
        data = dict()
        if name:
            data["name"] = name
        if slug:
            data["slug"] = slug
        response = self.client.patch(
            f"/clusters/{cluster_id}",
            params=dict(workspace=str(self.id)),
            data=data,
        )
        handle_error_with_response(response)
        return ClusterInfo.model_validate(response.json())

    @property
    def available_versions(self) -> list[str]:
        response = self.client.get("/versions")
        handle_error_with_response(response)
        return response.json()

    def migrate_cluster(self, cluster_id: str, version: str | None = None):
        data = dict()
        if version:
            data["version"] = version

        response = self.client.put(
            f"/clusters/{cluster_id}",
            params=dict(workspace=str(self.id)),
            data=data,
            # NOTE: Sometimes this command takes a little longer
            timeout=10,
        )
        handle_error_with_response(response)

    def get_payment_stream(self, cluster: ClusterInfo, chain_id: int) -> Stream | None:
        response = self.client.get(
            f"/clusters/{cluster.id}/stream",
            params=dict(workspace=str(self.id)),
        )
        handle_error_with_response(response)

        if not (raw_stream_info := response.json()):
            return None

        stream_info = StreamInfo.model_validate(raw_stream_info)

        if not stream_info.chain_id == chain_id:
            return None

        return Stream(manager=StreamManager(stream_info.manager), id=stream_info.stream_id)

    def update(
        self,
        name: str | None = None,
        slug: str | None = None,
    ) -> "Workspace":
        data = dict()
        if name:
            data["name"] = name
        if slug:
            data["slug"] = slug
        response = self.client.patch(
            f"/workspaces/{self.id}",
            data=data,
        )
        handle_error_with_response(response)
        return Workspace.model_validate(response.json())

    def remove(self):
        response = self.client.delete(f"/workspaces/{self.id}")
        handle_error_with_response(response)


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
        workspace_slug: str | None = None,
        workspace_name: str | None = None,
    ) -> Workspace:
        response = self.post(
            "/workspaces",
            data=dict(slug=workspace_slug, name=workspace_name),
        )
        handle_error_with_response(response)
        new_workspace = Workspace.model_validate_json(response.text)
        self.workspaces.update({new_workspace.slug: new_workspace})  # NOTE: Update cache
        return new_workspace

    def get_stream_manager(self, chain_id: int) -> StreamManager:
        response = self.get(f"/streams/manager/{chain_id}")
        handle_error_with_response(response)
        return StreamManager(response.json())

    def get_accepted_tokens(self, chain_id: int) -> dict[str, ContractInstance]:
        response = self.get(f"/streams/tokens/{chain_id}")
        handle_error_with_response(response)
        return {
            token_info["symbol"]: Contract(token_info["address"]) for token_info in response.json()
        }
