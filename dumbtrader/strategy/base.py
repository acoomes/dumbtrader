"""Abstract base class for all strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum, auto

import polars as pl


class SignalType(Enum):
    BUY = auto()
    SELL = auto()
    CLOSE_LONG = auto()
    CLOSE_SHORT = auto()


@dataclass
class Signal:
    type: SignalType
    size: int = 1
    reason: str = ""


class Strategy(ABC):
    """Base class every strategy must implement."""

    name: str = "unnamed"

    @abstractmethod
    def on_bars(self, df: pl.DataFrame) -> list[Signal]:
        """Evaluate the latest bars and return zero or more signals.

        ``df`` is a Polars DataFrame with at least the columns
        ``open``, ``high``, ``low``, ``close``, ``volume`` plus any
        indicator columns the engine has computed.
        """
        ...
