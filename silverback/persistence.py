import json
import os
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Optional, TypeVar

from pydantic import BaseModel
from taskiq import TaskiqResult
from typing_extensions import Self  # Introduced 3.11

from .types import SilverbackID

_HandlerReturnType = TypeVar("_HandlerReturnType")


class SilverbackState(BaseModel):
    instance: str
    network: str
    # Last block number seen by runner
    last_block_seen: int
    # Last block number processed by a worker
    last_block_processed: int
    updated: datetime


class HandlerResult(TaskiqResult):
    instance: str
    network: str
    handler_id: str
    block_number: Optional[int]
    log_index: Optional[int]
    created: datetime

    @classmethod
    def from_taskiq(
        cls,
        ident: SilverbackID,
        handler_id: str,
        block_number: Optional[int],
        log_index: Optional[int],
        result: TaskiqResult,
    ) -> Self:
        return cls(
            instance=ident.identifier,
            network=ident.network_choice,
            handler_id=handler_id,
            block_number=block_number,
            log_index=log_index,
            created=datetime.now(timezone.utc),
            **result.dict(),
        )


class BasePersistentStore(ABC):
    @abstractmethod
    async def init(self):
        """Handle any async initialization from Silverback settings (e.g. migrations)."""
        ...

    @abstractmethod
    async def get_state(self, ident: SilverbackID) -> Optional[SilverbackState]:
        """Return the stored state for a Silverback instance"""
        ...

    @abstractmethod
    async def set_state(
        self, ident: SilverbackID, last_block_seen: int, last_block_processed: int
    ) -> Optional[SilverbackState]:
        """Set the stored state for a Silverback instance"""
        ...

    @abstractmethod
    async def get_latest_result(
        self, ident: SilverbackID, handler: Optional[str] = None
    ) -> Optional[HandlerResult]:
        """Return the latest result for a Silverback instance's handler"""
        ...

    @abstractmethod
    async def add_result(self, v: HandlerResult):
        """Store a result for a Silverback instance's handler"""
        ...


class SQLitePersistentStore(BasePersistentStore):
    """
    SQLite implementation of BasePersistentStore used to store application state and handler
    result data.

    Usage:

    To use SQLite persistent store, you must configure the following env vars:

    - `PERSISTENCE_CLASS`: `silverback.persistence.SQLitePersistentStore`
    - `SQLITE_PATH` (optional): A system file path or if blank it will be stored in-memory.
    """

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
        SELECT handler_id, block_number, log_index, execution_time, is_err, created,
            return_value_blob
        FROM silverback_result
        WHERE instance = ? AND network = ?
        ORDER BY created DESC
        LIMIT 1;
    """
    SQL_GET_HANDLER_LATEST = """
        SELECT handler_id, block_number, log_index, execution_time, is_err, created,
            return_value_blob
        FROM silverback_result
        WHERE instance = ? AND network = ? AND handler_id = ?
        ORDER BY created DESC
        LIMIT 1;
    """
    SQL_INSERT_RESULT = """
        INSERT INTO silverback_result (
            instance, network, handler_id, block_number, log_index, execution_time,
            is_err, created, return_value_blob
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
    """

    con: Optional[sqlite3.Connection]
    initialized: bool = False

    async def init(self):
        self.con = sqlite3.connect(os.environ.get("SQLITE_PATH", ":memory:"))

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
                is_err bool,
                created int,
                return_value_blob blob
            );
            CREATE UNIQUE INDEX IF NOT EXISTS silverback_state__instance
                ON silverback_state(instance, network);
            CREATE INDEX IF NOT EXISTS silverback_result__instance
                ON silverback_result (instance, network);
            CREATE INDEX IF NOT EXISTS silverback_result__handler
                ON silverback_result (instance, network, handler_id);
            CREATE INDEX IF NOT EXISTS silverback_result__is_err
                ON silverback_result (is_err);
            COMMIT;
        """
        )
        cur.close()

        if not self.con:
            raise Exception("Failed to setup SQLite connection")

        self.initialized = True

    async def get_state(self, ident: SilverbackID) -> Optional[SilverbackState]:
        if not self.initialized:
            await self.init()

        assert self.con is not None

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

    async def set_state(
        self, ident: SilverbackID, last_block_seen: int, last_block_processed: int
    ) -> Optional[SilverbackState]:
        if not self.initialized:
            await self.init()

        assert self.con is not None

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
        self, ident: SilverbackID, handler: Optional[str] = None
    ) -> Optional[HandlerResult]:
        if not self.initialized:
            await self.init()

        assert self.con is not None

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
            is_err=row[4],
            created=datetime.fromtimestamp(row[5], timezone.utc),
            return_value=json.loads(row[6]),
        )

    async def add_result(self, v: HandlerResult):
        if not self.initialized:
            await self.init()

        assert self.con is not None

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
                v.is_err,
                v.created,
                json.dumps(v.return_value),
            ),
        )

        cur.close()
        self.con.commit()
