import asyncio
import json
from enum import Enum
from typing import AsyncGenerator, Dict, List, Optional

from ape.logging import logger
from websockets import client as ws_client


class SubscriptionType(Enum):
    BLOCKS = "newHeads"
    EVENTS = "logs"


class Web3SubscriptionsManager:
    websocket_reconnect_max_tries: int = 3
    rpc_response_timeout_count: int = 10
    subscription_polling_time: float = 0.1  # secs

    def __init__(self, ws_provider_uri: str):
        # TODO: Temporary until a more permanent solution is added to ProviderAPI
        if "infura" in ws_provider_uri and "ws/v3" not in ws_provider_uri:
            ws_provider_uri = ws_provider_uri.replace("v3", "ws/v3")

        self._ws_provider_uri = ws_provider_uri

        # Stateful
        self._connection: Optional[ws_client.WebSocketClientProtocol] = None
        self._last_request: int = 0
        self._subscriptions: Dict[str, asyncio.Queue] = {}
        self._rpc_msg_buffer: List[dict] = []
        self._ws_lock = asyncio.Lock()

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} uri={self._ws_provider_uri}>"

    async def __aenter__(self) -> "Web3SubscriptionsManager":
        self.connection = await ws_client.connect(self._ws_provider_uri)
        return self

    def __aiter__(self) -> "Web3SubscriptionsManager":
        return self

    async def __anext__(self) -> str:
        if not self.connection:
            raise StopAsyncIteration

        message = await self.connection.recv()
        # TODO: Handle retries when connection breaks

        response = json.loads(message)
        if response.get("method") == "eth_subscription":
            sub_params: dict = response.get("params", {})
            if not (sub_id := sub_params.get("subscription")) or not isinstance(sub_id, str):
                logger.debug(f"Corrupted subscription data: {response}")
                return response

            if sub_id not in self._subscriptions:
                self._subscriptions[sub_id] = asyncio.Queue()

            await self._subscriptions[sub_id].put(sub_params.get("result", {}))

        else:
            self._rpc_msg_buffer.append(response)

        return response

    def _create_request(self, method: str, params: list) -> dict:
        self._last_request += 1
        return {
            "jsonrpc": "2.0",
            "id": self._last_request,
            "method": method,
            "params": params,
        }

    async def _get_response(self, request_id: int) -> dict:
        if buffer := self._rpc_msg_buffer:
            for idx, data in enumerate(buffer):
                if data.get("id") == request_id:
                    self._rpc_msg_buffer.pop(idx)
                    return data

        async with self._ws_lock:
            tries = 0
            while tries < self.rpc_response_timeout_count:
                if self._rpc_msg_buffer and self._rpc_msg_buffer[-1].get("id") == request_id:
                    return self._rpc_msg_buffer.pop()

                # NOTE: Python <3.10 does not support `anext` function
                await self.__anext__()  # Keep pulling until we get a response

        raise RuntimeError("Timeout waiting for response.")

    async def subscribe(self, type: SubscriptionType, **filter_params) -> str:
        if not self.connection:
            raise ValueError("Connection required.")

        if type is SubscriptionType.BLOCKS and filter_params:
            raise ValueError("blocks subscription doesn't accept filter params.")

        request = self._create_request(
            "eth_subscribe",
            [type.value, filter_params] if type is SubscriptionType.EVENTS else [type.value],
        )
        await self.connection.send(json.dumps(request))
        response = await self._get_response(request.get("id") or self._last_request)

        sub_id = response.get("result")
        if not sub_id:
            # NOTE: Re-dumping message to avoid type-checking concerns.
            raise ValueError(f"Missing subscription ID in response: {json.dumps(response)}.")

        return sub_id

    async def get_subscription_data(self, sub_id: str) -> AsyncGenerator[dict, None]:
        while True:
            if not (queue := self._subscriptions.get(sub_id)) or queue.empty():
                async with self._ws_lock:
                    # Keep pulling until a message comes to process
                    # NOTE: Python <3.10 does not support `anext` function
                    await self.__anext__()
            else:
                yield await queue.get()

    async def unsubscribe(self, sub_id: str) -> bool:
        if sub_id not in self._subscriptions:
            raise ValueError(f"Unknown sub_id '{sub_id}'")

        if not self.connection:
            # Nothing to unsubscribe.
            return True

        request = self._create_request("eth_unsubscribe", [sub_id])
        await self.connection.send(json.dumps(request))

        response = await self._get_response(request.get("id") or self._last_request)
        if success := response.get("result", False):
            del self._subscriptions[sub_id]  # NOTE: Save memory

        return success

    async def __aexit__(self, exc_type, exc, tb):
        try:
            # Try to gracefully unsubscribe to all events
            await asyncio.gather(*(self.unsubscribe(sub_id) for sub_id in self._subscriptions))

        finally:
            # Disconnect and release websocket
            await self.connection.close()
