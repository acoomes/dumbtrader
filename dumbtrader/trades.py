"""Persistent trade ledger backed by SQLite."""

from __future__ import annotations

import logging
from dataclasses import dataclass, fields
from pathlib import Path

import aiosqlite

logger = logging.getLogger("dumbtrader.trades")

_DB_PATH = Path(__file__).resolve().parent.parent / "trades.db"

_CREATE_TABLE = """\
CREATE TABLE IF NOT EXISTS trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL,
    instrument  TEXT NOT NULL,
    contract_id TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    side        INTEGER NOT NULL,
    size        INTEGER NOT NULL,
    order_id    INTEGER,
    success     INTEGER NOT NULL DEFAULT 1,
    error_msg   TEXT,
    fill_price  REAL,
    pnl         REAL,
    strategy    TEXT,
    reason      TEXT,
    dry_run     INTEGER NOT NULL DEFAULT 0
);
"""


@dataclass
class TradeRecord:
    timestamp: str
    instrument: str
    contract_id: str
    signal_type: str
    side: int
    size: int
    order_id: int | None = None
    success: int = 1
    error_msg: str | None = None
    fill_price: float | None = None
    pnl: float | None = None
    strategy: str | None = None
    reason: str | None = None
    dry_run: int = 0


_INSERT_COLS = [f.name for f in fields(TradeRecord)]
_INSERT_SQL = (
    f"INSERT INTO trades ({', '.join(_INSERT_COLS)}) "
    f"VALUES ({', '.join('?' for _ in _INSERT_COLS)})"
)


class TradeStore:
    """Async SQLite wrapper for the trade ledger."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._path = db_path or _DB_PATH
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        self._db = await aiosqlite.connect(str(self._path))
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute(_CREATE_TABLE)
        await self._db.commit()
        logger.info("Trade store ready: %s", self._path)

    async def record_trade(self, rec: TradeRecord) -> None:
        assert self._db is not None
        values = tuple(getattr(rec, f.name) for f in fields(TradeRecord))
        await self._db.execute(_INSERT_SQL, values)
        await self._db.commit()
        logger.debug("Recorded trade: %s %s", rec.signal_type, rec.instrument)

    async def get_last_entry(self, instrument: str) -> tuple[float, int] | None:
        """Return (fill_price, side) of the most recent BUY/SELL for *instrument*."""
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT fill_price, side FROM trades "
            "WHERE instrument = ? AND signal_type IN ('BUY', 'SELL') "
            "AND success = 1 AND fill_price IS NOT NULL "
            "ORDER BY id DESC LIMIT 1",
            (instrument,),
        )
        row = await cursor.fetchone()
        return (row[0], row[1]) if row else None

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None
