import pickle
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Annotated, Any, Dict, Optional, Type

from ape.logging import logger
from pydantic import BaseModel
from taskiq import TaskiqResult
from typing_extensions import Self  # Introduced 3.11

from .types import SilverbackIdent


class SilverbackState(BaseModel):
    instance: str
    network: str
    # Last block number seen by runner
    last_block_seen: int
    # Last block number processed by a worker
    last_block_processed: int
    updated: datetime


class HandlerResult(BaseModel):
    instance: str
    network: str
    handler_id: str
    block_number: Optional[int]
    log_index: Optional[int]
    execution_time: float
    # TODO: upcoming feature in taskiq
    # labels: Dict[str]
    # TODO: Use computed field with pydantic v2
    return_value_blob: Optional[bytes]  # pickled data
    created: datetime

    @classmethod
    def from_taskiq(
        cls,
        ident: SilverbackIdent,
        handler_id: str,
        block_number: int | None,
        log_index: int | None,
        result: TaskiqResult,
    ) -> Self:
        return cls(
            instance=ident.identifier,
            network=ident.network_choice,
            handler_id=handler_id,
            block_number=block_number,
            log_index=log_index,
            execution_time=result.execution_time,
            # labels=result.labels,
            return_value_blob=pickle.dumps(result.return_value),
            created=datetime.now(timezone.utc),
        )


class BasePersistentStorage(ABC):
    @abstractmethod
    async def get_instance_state(self, ident: SilverbackIdent) -> Optional[SilverbackState]:
        ...

    @abstractmethod
    async def set_instance_state(
        self, ident: SilverbackIdent, last_block_seen: int, last_block_processed: int
    ) -> Optional[SilverbackState]:
        ...

    @abstractmethod
    async def get_latest_result(
        self, instance: SilverbackIdent, handler: Optional[str] = None
    ) -> Optional[HandlerResult]:
        ...

    @abstractmethod
    async def add_result(self, v: HandlerResult):
        ...


async def init_mongo(mongo_uri: str) -> Optional[BasePersistentStorage]:
    try:
        import pymongo
        from beanie import Document, Indexed, init_beanie
        from beanie.odm.operators.update.general import Set
        from motor.core import AgnosticClient
        from motor.motor_asyncio import AsyncIOMotorClient
    except ImportError as err:
        print(err)
        logger.warning("MongoDB was initialized by dependencies are not installed")
        return None

    # NOTE: Ignoring an inheritence issue with pydantic's Config class.  Goes away with v2
    class SilverbackStateDoc(SilverbackState, Document):  # type: ignore
        instance: Annotated[str, Indexed(str)]
        network: Annotated[str, Indexed(str)]
        last_block_seen: int
        last_block_processed: int
        updated: datetime

        class Settings:
            name = "state"
            indexes = [
                [
                    ("instance", pymongo.TEXT),
                    ("network", pymongo.TEXT),
                ],
            ]

        def to_silberback_state(self) -> SilverbackState:
            return SilverbackState(
                instance=self.instance,
                network=self.network,
                last_block_seen=self.last_block_seen,
                last_block_processed=self.last_block_processed,
                updated=self.updated,
            )

    # NOTE: Ignoring an inheritence issue with pydantic's Config class.  Goes away with v2
    class HandlerResultDoc(HandlerResult, Document):  # type: ignore
        # NOTE: Redefining these to annotate with indexed type
        instance: Annotated[str, Indexed(str)]
        network: Annotated[str, Indexed(str)]
        handler_id: Annotated[str, Indexed(str)]

        class Settings:
            name = "result"
            indexes = [
                [
                    ("instance", pymongo.TEXT),
                    ("network", pymongo.TEXT),
                    ("handler", pymongo.TEXT),
                ],
            ]

        @classmethod
        def from_handler_result(cls, result: HandlerResult) -> Self:
            return cls(
                instance=result.instance,
                network=result.network,
                handler_id=result.handler_id,
                block_number=result.block_number,
                log_index=result.log_index,
                execution_time=result.execution_time,
                return_value_blob=result.return_value_blob,
                created=result.created,
            )

        def to_handler_result(self) -> HandlerResult:
            return HandlerResult(
                instance=self.instance,
                network=self.network,
                handler_id=self.handler,
                block_number=self.block_number,
                log_index=self.log_index,
                execution_time=self.execution_time,
                return_value_blob=self.return_value_blob,
                created=self.created,
            )

    class MongoStorage(BasePersistentStorage):
        client: AgnosticClient

        async def get_instance_state(self, ident: SilverbackIdent) -> Optional[SilverbackState]:
            res = await SilverbackStateDoc.find_one(
                SilverbackStateDoc.instance == ident.identifier,
                SilverbackStateDoc.network == ident.network_choice,
            )

            if res is None:
                return None

            return res.to_silberback_state()

        async def set_instance_state(
            self, ident: SilverbackIdent, last_block_seen: int, last_block_processed: int
        ) -> Optional[SilverbackState]:
            now_utc = datetime.now(timezone.utc)

            state = await SilverbackStateDoc.find_one(
                SilverbackStateDoc.instance == ident.identifier,
                SilverbackStateDoc.network == ident.network_choice,
            )

            if state is not None:
                await state.set(
                    # Unreported type error?  Confiremd working
                    {
                        SilverbackStateDoc.last_block_seen: last_block_seen,  # type: ignore
                        SilverbackStateDoc.last_block_processed: last_block_processed,  # type: ignore
                        SilverbackStateDoc.updated: now_utc,  # type: ignore
                    }
                )
            else:
                state = SilverbackStateDoc(
                    instance=ident.identifier,
                    network=ident.network_choice,
                    last_block_seen=last_block_seen,
                    last_block_processed=last_block_processed,
                    updated=now_utc,
                )
                await state.create()

            # TODO: Why no work?
            # await SilverbackStateDoc.find_one(
            #     SilverbackStateDoc.instance == ident.identifier,
            #     SilverbackStateDoc.network == ident.network_choice,
            # ).upsert(
            #     Set(
            #         {
            #             SilverbackStateDoc.last_block_seen: last_block_seen,
            #             SilverbackStateDoc.last_block_processed: last_block_processed,
            #             SilverbackStateDoc.updated: now_utc,
            #         }
            #     ),
            #     on_insert=SilverbackStateDoc(
            #         instance=ident.identifier,
            #         network=ident.network_choice,
            #         last_block_seen=last_block_seen,
            #         last_block_processed=last_block_processed,
            #         updated=now_utc,
            #     ),
            # )

            return state

        async def get_latest_result(
            self, ident: SilverbackIdent, handler_id: Optional[str] = None
        ) -> Optional[HandlerResult]:
            query = HandlerResultDoc.find(
                HandlerResultDoc.instance == ident.identifier,
                HandlerResultDoc.network == ident.network_choice,
            )

            if handler_id:
                query.find(HandlerResultDoc.handler_id == handler_id)

            res = await query.sort("-created").first_or_none()

            if res is None:
                return None

            return res.to_handler_result()

        async def add_result(self, result: HandlerResult):
            doc = HandlerResultDoc.from_handler_result(result)
            # Type annotation error: https://github.com/roman-right/beanie/issues/679
            await doc.insert()  # type: ignore

    storage = MongoStorage()
    client: AgnosticClient = AsyncIOMotorClient(mongo_uri)

    await init_beanie(
        database=client.db_name,
        # Type annotation error: https://github.com/roman-right/beanie/issues/670
        document_models=[
            HandlerResultDoc,
            SilverbackStateDoc,
        ],  # type: ignore
    )

    return storage
