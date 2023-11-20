import pickle
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel
from taskiq import TaskiqResult
from typing_extensions import Self  # Introduced 3.11

from .types import SilverbackIdent, SilverbackSettings


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
    def __init__(self, settings: SilverbackSettings):
        self.settings = settings

    @abstractmethod
    async def init(self):
        """Handle any async initialization from Silverback settings (e.g. migrations)."""
        ...

    @abstractmethod
    async def get_instance_state(self, ident: SilverbackIdent) -> Optional[SilverbackState]:
        """Return the stored state for a Silverback instance"""
        ...

    @abstractmethod
    async def set_instance_state(
        self, ident: SilverbackIdent, last_block_seen: int, last_block_processed: int
    ) -> Optional[SilverbackState]:
        """Set the stored state for a Silverback instance"""
        ...

    @abstractmethod
    async def get_latest_result(
        self, ident: SilverbackIdent, handler: Optional[str] = None
    ) -> Optional[HandlerResult]:
        """Return the latest result for a Silverback instance's handler"""
        ...

    @abstractmethod
    async def add_result(self, v: HandlerResult):
        """Store a result for a Silverback instance's handler"""
        ...


class SQLitePersistentStorage(BasePersistentStorage):
    SQL_GET_STATE = """
        SELECT last_block_seen, last_block_processed, updated
        FROM silverback_state
        WHERE instance = ? AND network = ?;
    """
    SQL_INSERT_STATE = """
        INSERT INTO silverback_state (
            instance, network, last_block_seen, last_block_processed, updated
        )
        VALUES (?, ?, ?, ?, ?);
    """
    SQL_UPDATE_STATE = """
        UPDATE silverback_state
        SET last_block_seen = ?, last_block_processed = ?, updated = ?
        WHERE instance = ? AND network = ?;
    """
    SQL_GET_RESULT_LATEST = """
        SELECT handler_id, block_number, log_index, execution_time, return_value_blob, created
        FROM silverback_result
        WHERE instance = ? AND network = ?
        ORDER BY created DESC
        LIMIT 1;
    """
    SQL_GET_HANDLER_LATEST = """
        SELECT handler_id, block_number, log_index, execution_time, return_value_blob, created
        FROM silverback_result
        WHERE instance = ? AND network = ? AND handler_id = ?
        ORDER BY created DESC
        LIMIT 1;
    """
    SQL_INSERT_RESULT = """
        INSERT INTO silverback_result (
            instance, network, handler_id, block_number, log_index, execution_time,
            return_value_blob, created
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?);
    """

    async def init(self):
        self.con = sqlite3.connect(self.settings.PERSISTENCE_URI or ":memory:")

        cur = self.con.cursor()
        cur.executescript(
            """
            BEGIN;
            CREATE TABLE IF NOT EXISTS silverback_state (
                instance text,
                network text,
                last_block_seen int,
                last_block_processed int,
                updated int
            );
            CREATE TABLE IF NOT EXISTS silverback_result (
                instance text,
                network text,
                handler_id text,
                block_number int,
                log_index int,
                execution_time real,
                return_value_blob blob,
                created int
            );
            CREATE UNIQUE INDEX IF NOT EXISTS silverback_state__instance
                ON silverback_state(instance, network);
            CREATE INDEX IF NOT EXISTS silverback_result__instance
                ON silverback_result (instance, network);
            CREATE INDEX IF NOT EXISTS silverback_result__handler
                ON silverback_result (instance, network, handler_id);
            COMMIT;
        """
        )
        cur.close()

    async def get_instance_state(self, ident: SilverbackIdent) -> Optional[SilverbackState]:
        cur = self.con.cursor()
        res = cur.execute(
            self.SQL_GET_STATE,
            (ident.identifier, ident.network_choice),
        )
        row = res.fetchone()

        cur.close()

        if row is None:
            return None

        return SilverbackState(
            instance=ident.identifier,
            network=ident.network_choice,
            last_block_seen=row[0],
            last_block_processed=row[1],
            updated=datetime.fromtimestamp(row[2], timezone.utc),
        )

    async def set_instance_state(
        self, ident: SilverbackIdent, last_block_seen: int, last_block_processed: int
    ) -> Optional[SilverbackState]:
        cur = self.con.cursor()
        res = cur.execute(
            self.SQL_GET_STATE,
            (ident.identifier, ident.network_choice),
        )
        row = res.fetchone()

        now = datetime.now(timezone.utc)
        now_stamp = int(now.timestamp())

        if row is None:
            cur.execute(
                self.SQL_INSERT_STATE,
                (
                    ident.identifier,
                    ident.network_choice,
                    last_block_seen,
                    last_block_processed,
                    now_stamp,
                ),
            )
        else:
            cur.execute(
                self.SQL_UPDATE_STATE,
                (
                    last_block_seen,
                    last_block_processed,
                    now_stamp,
                    ident.identifier,
                    ident.network_choice,
                ),
            )

        cur.close()
        self.con.commit()

        return SilverbackState(
            instance=ident.identifier,
            network=ident.network_choice,
            last_block_seen=last_block_seen,
            last_block_processed=last_block_processed,
            updated=now,
        )

    async def get_latest_result(
        self, ident: SilverbackIdent, handler: Optional[str] = None
    ) -> Optional[HandlerResult]:
        cur = self.con.cursor()

        if handler is not None:
            res = cur.execute(
                self.SQL_GET_HANDLER_LATEST,
                (ident.identifier, ident.network_choice, handler),
            )
        else:
            res = cur.execute(
                self.SQL_GET_RESULT_LATEST,
                (ident.identifier, ident.network_choice),
            )

        row = res.fetchone()

        cur.close()

        if row is None:
            return None

        return HandlerResult(
            instance=ident.identifier,
            network=ident.network_choice,
            handler_id=row[0],
            block_number=row[1],
            log_index=row[2],
            execution_time=row[3],
            return_value_blob=row[4],
            created=datetime.fromtimestamp(row[5], timezone.utc),
        )

    async def add_result(self, v: HandlerResult):
        cur = self.con.cursor()

        cur.execute(
            self.SQL_INSERT_RESULT,
            (
                v.instance,
                v.network,
                v.handler_id,
                v.block_number,
                v.log_index,
                v.execution_time,
                v.return_value_blob,
                v.created,
            ),
        )

        cur.close()
        self.con.commit()
