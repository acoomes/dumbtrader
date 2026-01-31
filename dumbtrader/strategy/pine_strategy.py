"""Strategy loaded from a Pine Script file."""

from __future__ import annotations

from pathlib import Path

import polars as pl

from dumbtrader.pine.parser import parse
from dumbtrader.pine.runtime import PineRuntime
from dumbtrader.strategy.base import Signal, Strategy


class PineStrategy(Strategy):
    """Parses and evaluates a Pine Script file on each bar update."""

    def __init__(self, path: str | Path) -> None:
        path = Path(path)
        self.name = path.stem
        source = path.read_text()
        self._ast = parse(source)
        self._runtime = PineRuntime(self._ast)

    def on_bars(self, df: pl.DataFrame) -> list[Signal]:
        return self._runtime.evaluate(df)
