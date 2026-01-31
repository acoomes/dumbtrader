"""Thin wrappers around project-x-py indicators.

Each function takes a Polars DataFrame and returns it with the indicator
column(s) appended.  The wrapper names match what YAML strategies reference.
"""

from __future__ import annotations

from typing import Any

import polars as pl
from project_x_py.indicators import ATR, BBANDS, EMA, MACD, RSI, SMA


def compute_indicator(df: pl.DataFrame, kind: str, params: dict[str, Any]) -> pl.DataFrame:
    """Compute a named indicator and return the augmented DataFrame.

    ``kind`` must be one of: sma, ema, rsi, macd, bbands, atr.
    ``params`` are forwarded to the underlying project-x-py function.
    """
    fn = _REGISTRY.get(kind)
    if fn is None:
        raise ValueError(f"Unknown indicator: {kind!r}  (available: {sorted(_REGISTRY)})")
    return fn(df, **params)


# ---------------------------------------------------------------------------
# Individual wrappers – keep the signatures aligned with project-x-py
# ---------------------------------------------------------------------------

def sma(df: pl.DataFrame, *, period: int = 20, column: str = "close") -> pl.DataFrame:
    return SMA(df, column=column, period=period)


def ema(df: pl.DataFrame, *, period: int = 20, column: str = "close") -> pl.DataFrame:
    return EMA(df, column=column, period=period)


def rsi(df: pl.DataFrame, *, period: int = 14, column: str = "close") -> pl.DataFrame:
    return RSI(df, column=column, period=period)


def macd(
    df: pl.DataFrame,
    *,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
    column: str = "close",
) -> pl.DataFrame:
    return MACD(df, column=column, fast_period=fast_period, slow_period=slow_period, signal_period=signal_period)


def bbands(
    df: pl.DataFrame,
    *,
    period: int = 20,
    std_dev: float = 2.0,
    column: str = "close",
) -> pl.DataFrame:
    return BBANDS(df, column=column, period=period, std_dev=std_dev)


def atr(
    df: pl.DataFrame,
    *,
    period: int = 14,
    high_column: str = "high",
    low_column: str = "low",
    close_column: str = "close",
) -> pl.DataFrame:
    return ATR(df, high_column=high_column, low_column=low_column, close_column=close_column, period=period)


_REGISTRY: dict[str, Any] = {
    "sma": sma,
    "ema": ema,
    "rsi": rsi,
    "macd": macd,
    "bbands": bbands,
    "atr": atr,
}
