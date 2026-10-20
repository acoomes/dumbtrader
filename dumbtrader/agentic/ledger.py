"""Persistent ledger for the agentic loop: decisions, rollouts, lessons.

This is deliberately a *synchronous* SQLite store, separate from the engine's
async :class:`dumbtrader.trades.TradeStore`.  The loop is a weekly batch ritual
run by a human-in-the-loop, not a hot async path, and sync code keeps the
review/confirm CLI simple and obvious.

The ledger is the loop's memory of what was *actually run* — including every
override.  These loops die when a human silently deviates and then the
reflection analyzes a policy that was never actually executed, so adherence is
a first-class column, not an afterthought.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("dumbtrader.agentic.ledger")

_DB_PATH = Path(__file__).resolve().parents[2] / "agentic_loop.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Decision status lifecycle: proposed -> confirmed|rejected -> executed|skipped
PROPOSED = "proposed"
CONFIRMED = "confirmed"
REJECTED = "rejected"
EXECUTED = "executed"
SKIPPED = "skipped"


@dataclass
class Decision:
    """One policy-driven action in a rollout, tracked end to end."""

    iteration: int
    policy_version: str
    timestamp: str
    symbol: str
    action: str  # BUY / SELL / CLOSE / HOLD
    proposed_shares: float
    proposed_notional: float
    rationale: str  # which rule fired
    regime: str
    status: str = PROPOSED
    # Adherence: did the human run the policy as proposed?
    followed_policy: int = 1  # 1 == followed, 0 == overridden
    override_reason: str | None = None
    # Filled in on/after execution:
    fill_price: float | None = None
    executed_shares: float | None = None
    pnl: float | None = None
    id: int | None = None


@dataclass
class Rollout:
    """One iteration of the loop (one week)."""

    iteration: int
    policy_version: str
    started_at: str
    window_label: str  # e.g. "2026-W24"
    benchmark: str
    equity_start: float
    equity_end: float | None = None
    benchmark_return_pct: float | None = None
    regime: str = ""
    notes: str = ""
    id: int | None = None


@dataclass
class Lesson:
    """A distilled, regime-tagged lesson — written only after the gate clears."""

    iteration: int
    timestamp: str
    from_version: str
    to_version: str
    hypothesis: str
    rule_target: str
    regime: str
    evidence_n: int
    gate_cleared: int  # 1/0
    lesson: str
    id: int | None = None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS rollouts (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    iteration            INTEGER NOT NULL,
    policy_version       TEXT NOT NULL,
    started_at           TEXT NOT NULL,
    window_label         TEXT NOT NULL,
    benchmark            TEXT NOT NULL,
    equity_start         REAL NOT NULL,
    equity_end           REAL,
    benchmark_return_pct REAL,
    regime               TEXT,
    notes                TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    iteration        INTEGER NOT NULL,
    policy_version   TEXT NOT NULL,
    timestamp        TEXT NOT NULL,
    symbol           TEXT NOT NULL,
    action           TEXT NOT NULL,
    proposed_shares  REAL NOT NULL,
    proposed_notional REAL NOT NULL,
    rationale        TEXT NOT NULL,
    regime           TEXT NOT NULL,
    status           TEXT NOT NULL,
    followed_policy  INTEGER NOT NULL DEFAULT 1,
    override_reason  TEXT,
    fill_price       REAL,
    executed_shares  REAL,
    pnl              REAL
);
CREATE TABLE IF NOT EXISTS lessons (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    iteration     INTEGER NOT NULL,
    timestamp     TEXT NOT NULL,
    from_version  TEXT NOT NULL,
    to_version    TEXT NOT NULL,
    hypothesis    TEXT NOT NULL,
    rule_target   TEXT NOT NULL,
    regime        TEXT NOT NULL,
    evidence_n    INTEGER NOT NULL,
    gate_cleared  INTEGER NOT NULL,
    lesson        TEXT NOT NULL
);
"""


def _insert(conn: sqlite3.Connection, table: str, obj: object) -> int:
    data = {k: v for k, v in asdict(obj).items() if k != "id"}
    cols = ", ".join(data)
    placeholders = ", ".join("?" for _ in data)
    cur = conn.execute(
        f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", tuple(data.values())
    )
    conn.commit()
    return int(cur.lastrowid)


def _row_to(cls, row: sqlite3.Row):
    names = {f.name for f in fields(cls)}
    return cls(**{k: row[k] for k in row.keys() if k in names})


class LoopLedger:
    """Synchronous SQLite ledger for the agentic loop."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._path = db_path or _DB_PATH
        self._conn = sqlite3.connect(str(self._path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        logger.info("Loop ledger ready: %s", self._path)

    # -- rollouts ------------------------------------------------------
    def start_rollout(self, rollout: Rollout) -> int:
        rollout.id = _insert(self._conn, "rollouts", rollout)
        return rollout.id

    def finish_rollout(
        self,
        rollout_id: int,
        *,
        equity_end: float,
        benchmark_return_pct: float,
        notes: str = "",
    ) -> None:
        self._conn.execute(
            "UPDATE rollouts SET equity_end=?, benchmark_return_pct=?, notes=? "
            "WHERE id=?",
            (equity_end, benchmark_return_pct, notes, rollout_id),
        )
        self._conn.commit()

    def latest_iteration(self) -> int:
        cur = self._conn.execute("SELECT MAX(iteration) AS n FROM rollouts")
        row = cur.fetchone()
        return int(row["n"]) if row and row["n"] is not None else 0

    # -- decisions -----------------------------------------------------
    def record_decision(self, decision: Decision) -> int:
        if not decision.timestamp:
            decision.timestamp = _now()
        decision.id = _insert(self._conn, "decisions", decision)
        return decision.id

    def update_decision(self, decision_id: int, **fields_to_set) -> None:
        if not fields_to_set:
            return
        cols = ", ".join(f"{k}=?" for k in fields_to_set)
        self._conn.execute(
            f"UPDATE decisions SET {cols} WHERE id=?",
            (*fields_to_set.values(), decision_id),
        )
        self._conn.commit()

    def decisions_for(self, iteration: int) -> list[Decision]:
        cur = self._conn.execute(
            "SELECT * FROM decisions WHERE iteration=? ORDER BY id", (iteration,)
        )
        return [_row_to(Decision, r) for r in cur.fetchall()]

    def closed_decisions(self, policy_version: str | None = None) -> list[Decision]:
        """All executed decisions with a realized P&L (for the gate / scoring)."""
        sql = "SELECT * FROM decisions WHERE pnl IS NOT NULL"
        params: tuple = ()
        if policy_version is not None:
            sql += " AND policy_version=?"
            params = (policy_version,)
        sql += " ORDER BY id"
        cur = self._conn.execute(sql, params)
        return [_row_to(Decision, r) for r in cur.fetchall()]

    def adherence(self, iteration: int | None = None) -> float:
        sql = "SELECT AVG(followed_policy) AS a FROM decisions"
        params: tuple = ()
        if iteration is not None:
            sql += " WHERE iteration=?"
            params = (iteration,)
        row = self._conn.execute(sql, params).fetchone()
        return float(row["a"]) if row and row["a"] is not None else 1.0

    # -- lessons -------------------------------------------------------
    def record_lesson(self, lesson: Lesson) -> int:
        if not lesson.timestamp:
            lesson.timestamp = _now()
        lesson.id = _insert(self._conn, "lessons", lesson)
        return lesson.id

    def lessons(self) -> list[Lesson]:
        cur = self._conn.execute("SELECT * FROM lessons ORDER BY id")
        return [_row_to(Lesson, r) for r in cur.fetchall()]

    def close(self) -> None:
        self._conn.close()
