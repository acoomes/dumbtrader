"""Web dashboard for dumbtrader — FastAPI app + subprocess manager."""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_STATIC_DIR = Path(__file__).resolve().parent / "static"
_STRATEGIES_DIR = _PROJECT_ROOT / "strategies"
_BASE_CONFIG = _PROJECT_ROOT / "config.yaml"
_TRADES_DB = _PROJECT_ROOT / "trades.db"

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="dumbtrader dashboard")

# ---------------------------------------------------------------------------
# Subprocess state
# ---------------------------------------------------------------------------
_process: asyncio.subprocess.Process | None = None
_reader_task: asyncio.Task | None = None
_ws_clients: set[WebSocket] = set()
_current_config: dict[str, Any] = {}
_session_start: str | None = None  # ISO 8601 UTC timestamp when bot was started


async def _broadcast(line: str) -> None:
    """Send a log line to every connected WebSocket client."""
    dead: list[WebSocket] = []
    for ws in _ws_clients:
        try:
            await ws.send_text(line)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_clients.discard(ws)


async def _read_stream(stream: asyncio.StreamReader) -> None:
    """Read lines from a subprocess stream and broadcast them."""
    while True:
        raw = await stream.readline()
        if not raw:
            break
        line = raw.decode("utf-8", errors="replace").rstrip("\n").rstrip("\r")
        await _broadcast(line)


async def _watch_process() -> None:
    """Read stdout + stderr and wait for the process to exit."""
    global _process
    if _process is None:
        return
    tasks: list[asyncio.Task] = []
    if _process.stdout:
        tasks.append(asyncio.create_task(_read_stream(_process.stdout)))
    if _process.stderr:
        tasks.append(asyncio.create_task(_read_stream(_process.stderr)))
    await asyncio.gather(*tasks)
    await _process.wait()
    await _broadcast(f"[dashboard] bot exited with code {_process.returncode}")
    _process = None


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------
@app.get("/api/status")
async def api_status() -> dict:
    return {
        "running": _process is not None and _process.returncode is None,
        "config": _current_config,
        "session_start": _session_start,
    }


@app.get("/api/strategies")
async def api_strategies() -> list[str]:
    if not _STRATEGIES_DIR.is_dir():
        return []
    return sorted(
        f.name
        for f in _STRATEGIES_DIR.iterdir()
        if f.suffix in (".yaml", ".yml", ".pine")
    )


@app.post("/api/start")
async def api_start(body: dict[str, Any]) -> JSONResponse:
    global _process, _reader_task, _current_config, _session_start

    if _process is not None and _process.returncode is None:
        return JSONResponse({"error": "Bot is already running"}, status_code=409)

    instrument = body.get("instrument", "MNQ")
    strategy = body.get("strategy", "")
    mode = body.get("mode", "paper")

    if mode not in ("paper", "live"):
        return JSONResponse({"error": "mode must be 'paper' or 'live'"}, status_code=400)

    # Build a temporary config based on the existing config.yaml
    if _BASE_CONFIG.exists():
        with open(_BASE_CONFIG) as f:
            cfg = yaml.safe_load(f)
    else:
        cfg = {}

    cfg.setdefault("api", {})
    cfg.setdefault("trading", {})
    cfg.setdefault("strategy", {})

    cfg["trading"]["instrument"] = instrument
    cfg["trading"]["mode"] = mode
    if strategy:
        cfg["strategy"]["file"] = f"strategies/{strategy}"

    # Write temp config
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", prefix="dumbtrader_", delete=False, dir=str(_PROJECT_ROOT)
    )
    yaml.safe_dump(cfg, tmp)
    tmp.close()

    _current_config = {"instrument": instrument, "strategy": strategy, "mode": mode}
    _session_start = datetime.now(timezone.utc).isoformat()

    # Build command
    cmd = [sys.executable, "-u", "-m", "dumbtrader", "-c", tmp.name]
    if mode == "live":
        cmd.extend(["--live", "--yes"])

    await _broadcast(f"[dashboard] starting: {' '.join(cmd)}")

    _process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(_PROJECT_ROOT),
    )

    _reader_task = asyncio.create_task(_watch_process())
    return JSONResponse({"ok": True, "pid": _process.pid})


@app.post("/api/stop")
async def api_stop() -> JSONResponse:
    global _process
    if _process is None or _process.returncode is not None:
        return JSONResponse({"error": "Bot is not running"}, status_code=409)

    await _broadcast("[dashboard] stopping bot…")
    _process.terminate()
    try:
        await asyncio.wait_for(_process.wait(), timeout=10)
    except asyncio.TimeoutError:
        _process.kill()
        await _process.wait()
    await _broadcast(f"[dashboard] bot stopped (code {_process.returncode})")
    _process = None
    return JSONResponse({"ok": True})


def _zeroed_metrics() -> dict:
    return {
        "total_trades": 0,
        "successful": 0,
        "failed": 0,
        "wins": 0,
        "losses": 0,
        "breakeven": 0,
        "win_rate": 0.0,
        "total_pnl": 0.0,
        "avg_win": 0.0,
        "avg_loss": 0.0,
        "largest_win": 0.0,
        "largest_loss": 0.0,
        "recent_trades": [],
    }


def _query_metrics(cur: sqlite3.Cursor, since: str | None = None) -> dict:
    """Run metric queries, optionally filtered to trades after *since*."""
    filt = ""
    params: tuple = ()
    if since:
        filt = " AND timestamp >= ?"
        params = (since,)

    def q(sql: str) -> Any:
        cur.execute(sql, params)
        return cur.fetchone()[0]

    total = q(f"SELECT COUNT(*) FROM trades WHERE 1=1{filt}")
    successful = q(f"SELECT COUNT(*) FROM trades WHERE success = 1{filt}")
    failed = total - successful

    wins = q(f"SELECT COUNT(*) FROM trades WHERE pnl IS NOT NULL AND pnl > 0{filt}")
    losses = q(f"SELECT COUNT(*) FROM trades WHERE pnl IS NOT NULL AND pnl < 0{filt}")
    breakeven = q(f"SELECT COUNT(*) FROM trades WHERE pnl IS NOT NULL AND pnl = 0{filt}")

    decided = wins + losses
    win_rate = (wins / decided * 100) if decided > 0 else 0.0

    total_pnl = q(f"SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE pnl IS NOT NULL{filt}")
    avg_win = q(f"SELECT COALESCE(AVG(pnl), 0) FROM trades WHERE pnl IS NOT NULL AND pnl > 0{filt}")
    avg_loss = q(f"SELECT COALESCE(AVG(pnl), 0) FROM trades WHERE pnl IS NOT NULL AND pnl < 0{filt}")
    largest_win = q(f"SELECT COALESCE(MAX(pnl), 0) FROM trades WHERE pnl IS NOT NULL{filt}")
    largest_loss = q(f"SELECT COALESCE(MIN(pnl), 0) FROM trades WHERE pnl IS NOT NULL{filt}")

    cur.execute(
        "SELECT id, timestamp, instrument, signal_type, side, size, "
        "fill_price, pnl, strategy, reason, dry_run, success, error_msg "
        f"FROM trades WHERE 1=1{filt} ORDER BY id DESC LIMIT 20",
        params,
    )
    cols = [d[0] for d in cur.description]
    recent = [dict(zip(cols, row)) for row in cur.fetchall()]

    return {
        "total_trades": total,
        "successful": successful,
        "failed": failed,
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "win_rate": round(win_rate, 1),
        "total_pnl": round(total_pnl, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "largest_win": round(largest_win, 2),
        "largest_loss": round(largest_loss, 2),
        "recent_trades": recent,
    }


@app.get("/api/metrics")
async def api_metrics() -> dict:
    empty = {"all_time": _zeroed_metrics(), "session": _zeroed_metrics()}
    if not _TRADES_DB.exists():
        return empty

    try:
        con = sqlite3.connect(str(_TRADES_DB), timeout=2)
        con.execute("PRAGMA query_only = ON")
        cur = con.cursor()

        all_time = _query_metrics(cur)
        session = _query_metrics(cur, since=_session_start) if _session_start else _zeroed_metrics()

        con.close()
        return {"all_time": all_time, "session": session}
    except Exception:
        return empty


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------
@app.websocket("/ws/logs")
async def ws_logs(ws: WebSocket) -> None:
    await ws.accept()
    _ws_clients.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.discard(ws)


# ---------------------------------------------------------------------------
# Index page
# ---------------------------------------------------------------------------
_INDEX_HTML = (_STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/")
async def index() -> HTMLResponse:
    return HTMLResponse(_INDEX_HTML)
