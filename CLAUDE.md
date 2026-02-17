# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Dumbtrader is an async futures trading bot built on the ProjectX API (`project-x-py` SDK). It supports configurable strategies defined via YAML files or a Pine Script v5 subset.

## Common Commands

```bash
# Install in development mode
pip install -e .

# Run the bot
dumbtrader [-c CONFIG] [--live] [--dry-run]
python -m dumbtrader

# No test suite or linter is configured yet
```

## Git Workflow

- Do not make new feature/fix changes directly on `main`.
- Before starting new changes, create and switch to a dedicated branch (for example: `codex/<short-task-name>`).
- Keep `main` reserved for syncing from `origin/main`, hotfixes explicitly approved by the user, or merge results.
- If already on `main` with uncommitted work, branch off before making further edits.

## Architecture

The system follows an async event-driven loop:

**CLI (main.py)** → loads config + strategy → **Engine (engine.py)** → fetches bars, evaluates strategy, executes orders

### Key Components

- **Engine (`engine.py`)**: Async main loop that fetches bar data from ProjectX, passes it to the strategy, and executes resulting signals as market orders. Handles graceful shutdown (SIGINT/SIGTERM), dry-run mode, and position size limits.

- **Strategy Layer (`strategy/`)**: Abstract `Strategy` base class with `on_bars(df) -> list[Signal]`. Two implementations:
  - `YamlStrategy`: Declarative strategy from YAML files — defines inputs, indicators, and condition-based rules (crossover, comparisons).
  - `PineStrategy`: Parses and executes a subset of Pine Script v5 via a recursive-descent parser (`pine/parser.py`) and AST interpreter (`pine/runtime.py`).

- **Indicators (`indicators.py`)**: Wrappers around technical indicators (SMA, EMA, RSI, MACD, Bollinger Bands, ATR) that operate on Polars DataFrames.

- **Config (`config.py`)**: YAML-based configuration with `${ENV_VAR}` interpolation. Validates API credentials, trading mode, instrument, timeframe, and max position size.

### Signal Flow

1. Engine fetches OHLCV bar data into a Polars DataFrame
2. Strategy's `on_bars()` evaluates indicators and rules on the DataFrame
3. Returns `Signal` objects (BUY, SELL, CLOSE_LONG, CLOSE_SHORT)
4. Engine executes orders, respecting position size limits

### Live Mode Safety

Requires three conditions: config `mode: live`, `--live` CLI flag, and interactive user confirmation.

## Code Conventions

- Type hints with `from __future__ import annotations`
- Async/await for all I/O operations
- Polars DataFrames for bar data (row `-1` = current bar, `-2` = previous bar)
- Dataclasses for configuration objects
- Standard library `logging` module
