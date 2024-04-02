import json
import os
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, TypeVar

from ape.logging import get_logger
from pydantic import BaseModel, Field
from taskiq import TaskiqResult
from typing_extensions import Self  # Introduced 3.11

from .types import BaseDatapoint, Metrics, ScalarDatapoint, SilverbackID, scalar_types

_HandlerReturnType = TypeVar("_HandlerReturnType")


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
    block_number: int | None
    log_index: int | None
    created: datetime
    labels: dict[str, Any] = Field(default_factory=dict)
    execution_time: float
    metrics: Metrics
    error: Optional[str] = None

    @classmethod
    def _extract_metrics(cls, result: Any, handler_id: str) -> Metrics:
        if isinstance(result, BaseDatapoint):
            return {f"{handler_id}_result": result}

        elif isinstance(result, scalar_types):
            return {f"{handler_id}_result": ScalarDatapoint(data=result)}

        elif isinstance(result, dict):
            converted_result = {
                k: ScalarDatapoint(data=v) if not isinstance(v, BaseDatapoint) else v
                for k, v in result.items()
                if isinstance(v, (BaseDatapoint, *scalar_types))
            }
            if len(converted_result) < len(result):
                logger = get_logger(handler_id)
                logger.warning(f"Unhandled results: {len(result)-len(converted_result)}")

            return converted_result

        elif result is not None:
            logger = get_logger(handler_id)
            logger.warning(f"Cannot handle return type '{type(result.metrics)}'.")

        # else:
        return {}

    @classmethod
    def from_taskiq(
        cls,
        ident: SilverbackID,
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
            created=datetime.now(timezone.utc),
            labels=result.labels,
            execution_time=result.execution_time,
            error=str(result.error),
            metrics=cls._extract_metrics(result.return_value, handler_id),
        )


class BaseRecorder(ABC):
    @abstractmethod
    async def init(self):
        """Handle any async initialization from Silverback settings (e.g. migrations)."""
        ...

    @abstractmethod
    async def get_state(self, ident: SilverbackID) -> SilverbackState | None:
        """Return the stored state for a Silverback instance"""
        ...

    @abstractmethod
    async def set_state(
        self, ident: SilverbackID, last_block_seen: int, last_block_processed: int
    ) -> SilverbackState | None:
        """Set the stored state for a Silverback instance"""
        ...

    @abstractmethod
    async def get_latest_result(
        self, ident: SilverbackID, handler: str | None = None
    ) -> HandlerResult | None:
        """Return the latest result for a Silverback instance's handler"""
        ...

    @abstractmethod
    async def add_result(self, v: HandlerResult):
        """Store a result for a Silverback instance's handler"""
        ...


class SQLiteRecorder(BaseRecorder):
    """
    SQLite implementation of BaseRecorder used to store application state and handler
    result data.

    Usage:

    To use SQLite recorder, you must configure the following env vars:

    - `RECORDER_CLASS`: `silverback.recorder.SQLiteRecorder`
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
        SELECT handler_id, block_number, log_index, execution_time, error, created,
            metrics_blob
        FROM silverback_result
        WHERE instance = ? AND network = ?
        ORDER BY created DESC
        LIMIT 1;
    """
    SQL_GET_HANDLER_LATEST = """
        SELECT handler_id, block_number, log_index, execution_time, error, created,
            metrics_blob
        FROM silverback_result
        WHERE instance = ? AND network = ? AND handler_id = ?
        ORDER BY created DESC
        LIMIT 1;
    """
    SQL_INSERT_RESULT = """
        INSERT INTO silverback_result (
            instance, network, handler_id, block_number, log_index, execution_time,
            error, created, metrics_blob
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
    """

    con: sqlite3.Connection | None
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
                error text,
                created int,
                metrics_blob blob
            );
            CREATE UNIQUE INDEX IF NOT EXISTS silverback_state__instance
                ON silverback_state(instance, network);
            CREATE INDEX IF NOT EXISTS silverback_result__instance
                ON silverback_result (instance, network);
            CREATE INDEX IF NOT EXISTS silverback_result__handler
                ON silverback_result (instance, network, handler_id);
            CREATE INDEX IF NOT EXISTS silverback_result__error
                ON silverback_result (error);
            COMMIT;
        """
        )
        cur.close()

        if not self.con:
            raise Exception("Failed to setup SQLite connection")

        self.initialized = True

    async def get_state(self, ident: SilverbackID) -> SilverbackState | None:
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
    ) -> SilverbackState | None:
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
        self, ident: SilverbackID, handler: str | None = None
    ) -> HandlerResult | None:
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
            error=row[4],
            created=datetime.fromtimestamp(row[5], timezone.utc),
            metrics=json.loads(row[6]),
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
                v.error,
                v.created,
                json.dumps({n: m.model_dump_json() for n, m in v.metrics.items()}),
            ),
        )

        cur.close()
        self.con.commit()
