"""Strategy built from a YAML definition file."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import polars as pl
import yaml

from dumbtrader.indicators import compute_indicator
from dumbtrader.strategy.base import Signal, SignalType, Strategy

_logger = logging.getLogger("dumbtrader.strategy.yaml")

_ACTION_MAP: dict[str, SignalType] = {
    "buy": SignalType.BUY,
    "sell": SignalType.SELL,
    "close_long": SignalType.CLOSE_LONG,
    "close_short": SignalType.CLOSE_SHORT,
}


class YamlStrategy(Strategy):
    """Loads a strategy from a YAML file with indicators + rules."""

    def __init__(self, path: str | Path) -> None:
        with open(path) as f:
            raw = yaml.safe_load(f)

        self.name = raw.get("name", Path(path).stem)
        self._description = raw.get("description", "")
        self._inputs: dict[str, Any] = raw.get("inputs", {})
        self._raw_indicators: dict[str, dict] = raw.get("indicators", {})
        self._raw_rules: dict[str, dict] = raw.get("rules", {})

        # Resolve $var references in indicator defs
        self._indicators = self._resolve_indicators()

    # ------------------------------------------------------------------
    # Input / variable resolution
    # ------------------------------------------------------------------

    def _resolve_value(self, val: Any) -> Any:
        """Replace ``$var`` strings with values from ``inputs``."""
        if isinstance(val, str) and val.startswith("$"):
            key = val[1:]
            if key not in self._inputs:
                raise ValueError(f"Unknown input variable: {val!r}")
            return self._inputs[key]
        return val

    def _resolve_indicators(self) -> dict[str, dict]:
        resolved: dict[str, dict] = {}
        for name, defn in self._raw_indicators.items():
            d = {}
            for k, v in defn.items():
                d[k] = self._resolve_value(v)
            resolved[name] = d
        return resolved

    # ------------------------------------------------------------------
    # Indicator computation
    # ------------------------------------------------------------------

    def _compute_indicators(self, df: pl.DataFrame) -> pl.DataFrame:
        for col_name, defn in self._indicators.items():
            kind = defn["type"]
            params = {k: v for k, v in defn.items() if k != "type"}
            df = compute_indicator(df, kind, params)
            # The indicator library appends a column whose name depends on
            # the indicator type.  We rename the *last* column added so
            # the rules can reference it by the YAML key.
            last_col = df.columns[-1]
            if last_col != col_name:
                df = df.rename({last_col: col_name})
        return df

    # ------------------------------------------------------------------
    # Condition evaluation
    # ------------------------------------------------------------------

    @staticmethod
    def _eval_condition(cond: str, df: pl.DataFrame) -> bool:
        """Evaluate a simple condition string against the last two rows."""
        if df.height < 2:
            return False

        prev = df.row(-2, named=True)
        curr = df.row(-1, named=True)

        # crossover(a, b) – a crosses above b
        m = re.match(r"crossover\(\s*(\w+)\s*,\s*(\w+)\s*\)", cond)
        if m:
            a, b = m.group(1), m.group(2)
            return prev[a] <= prev[b] and curr[a] > curr[b]

        # crossunder(a, b) – a crosses below b
        m = re.match(r"crossunder\(\s*(\w+)\s*,\s*(\w+)\s*\)", cond)
        if m:
            a, b = m.group(1), m.group(2)
            return prev[a] >= prev[b] and curr[a] < curr[b]

        # a > b  or  a > number
        m = re.match(r"(\w+)\s*>\s*(\w+\.?\d*)", cond)
        if m:
            left = m.group(1)
            right = m.group(2)
            lv = curr[left]
            rv = curr[right] if right in curr else float(right)
            return lv > rv

        # a < b  or  a < number
        m = re.match(r"(\w+)\s*<\s*(\w+\.?\d*)", cond)
        if m:
            left = m.group(1)
            right = m.group(2)
            lv = curr[left]
            rv = curr[right] if right in curr else float(right)
            return lv < rv

        raise ValueError(f"Cannot parse condition: {cond!r}")

    # ------------------------------------------------------------------
    # Main evaluation
    # ------------------------------------------------------------------

    def on_bars(self, df: pl.DataFrame) -> list[Signal]:
        df = self._compute_indicators(df)

        if _logger.isEnabledFor(logging.DEBUG) and df.height >= 1:
            indicator_cols = list(self._indicators.keys())
            log_cols = [c for c in ["timestamp", "close"] + indicator_cols if c in df.columns]
            for i in (-2, -1):
                if df.height >= abs(i):
                    row = df.row(i, named=True)
                    vals = "  ".join(f"{c}={row[c]}" for c in log_cols)
                    _logger.debug("IndicatorBar[%d]: %s", i, vals)

        signals: list[Signal] = []
        for rule_name, rule in self._raw_rules.items():
            cond = rule["condition"]
            result = self._eval_condition(cond, df)
            if result:
                action = _ACTION_MAP.get(rule["action"])
                if action is None:
                    raise ValueError(f"Unknown action in rule {rule_name!r}: {rule['action']!r}")
                size = self._resolve_value(rule.get("size", 1))
                signals.append(Signal(type=action, size=int(size), reason=rule_name))
            elif _logger.isEnabledFor(logging.DEBUG):
                col_names = re.findall(r'\b([A-Za-z_]\w*)\b', cond)
                col_names = [c for c in col_names if c not in {"crossover", "crossunder"}]
                last = df.row(-1, named=True)
                prev = df.row(-2, named=True) if df.height >= 2 else {}
                vals = "  ".join(
                    f"{c}=[{prev.get(c, '?')}, {last.get(c, '?')}]"
                    for c in col_names
                    if c in df.columns
                )
                _logger.debug(
                    "Rule %r not triggered: condition=%r  values: %s",
                    rule_name, cond, vals or "(none)",
                )
        return signals
