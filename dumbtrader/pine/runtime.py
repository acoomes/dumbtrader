"""Pine Script interpreter/evaluator for the supported subset.

Evaluates the AST produced by ``parser.parse()`` against a Polars
DataFrame of bars.
"""

from __future__ import annotations

from typing import Any

import polars as pl

from dumbtrader.strategy.base import Signal, SignalType


class PineRuntime:
    """Execute a parsed Pine Script AST against bar data."""

    def __init__(self, ast: list[dict]) -> None:
        self.ast = ast
        self.variables: dict[str, Any] = {}
        self.signals: list[Signal] = []
        self._df: pl.DataFrame | None = None
        self._strategy_declared = False

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def evaluate(self, df: pl.DataFrame) -> list[Signal]:
        """Run the full AST against ``df`` and return generated signals."""
        self._df = df
        self.signals = []
        self.variables.clear()

        # Expose built-in series as the last bar's values
        if df.height > 0:
            last = df.row(-1, named=True)
            for col in ("close", "open", "high", "low", "volume"):
                if col in last:
                    self.variables[col] = last[col]

        for node in self.ast:
            self._exec(node)

        return self.signals

    # ------------------------------------------------------------------
    # Statement execution
    # ------------------------------------------------------------------

    def _exec(self, node: dict) -> Any:
        ntype = node["type"]
        if ntype == "assign":
            val = self._eval(node["value"])
            self.variables[node["name"]] = val
            return val
        if ntype == "if":
            cond = self._eval(node["condition"])
            if cond:
                for stmt in node["body"]:
                    self._exec(stmt)
            else:
                for stmt in node.get("else", []):
                    self._exec(stmt)
            return None
        if ntype == "expr":
            return self._eval(node["value"])
        return None

    # ------------------------------------------------------------------
    # Expression evaluation
    # ------------------------------------------------------------------

    def _eval(self, node: dict) -> Any:
        ntype = node["type"]

        if ntype == "number":
            return node["value"]
        if ntype == "string":
            return node["value"]
        if ntype == "bool":
            return node["value"]

        if ntype == "ident":
            name = node["name"]
            if name in self.variables:
                return self.variables[name]
            raise RuntimeError(f"Undefined variable: {name!r}")

        if ntype == "subscript":
            return self._eval_subscript(node)

        if ntype == "call":
            return self._eval_call(node)

        if ntype == "binop":
            return self._eval_binop(node)

        if ntype == "unaryop":
            return self._eval_unaryop(node)

        if ntype == "kwarg":
            return self._eval(node["value"])

        raise RuntimeError(f"Unknown node type: {ntype!r}")

    # ------------------------------------------------------------------
    # Operators
    # ------------------------------------------------------------------

    def _eval_binop(self, node: dict) -> Any:
        op = node["op"]
        left = self._eval(node["left"])
        right = self._eval(node["right"])

        if op == "+":
            return left + right
        if op == "-":
            return left - right
        if op == "*":
            return left * right
        if op == "/":
            return left / right if right != 0 else 0.0
        if op == "gt":
            return left > right
        if op == "lt":
            return left < right
        if op == "gte":
            return left >= right
        if op == "lte":
            return left <= right
        if op == "eq":
            return left == right
        if op == "neq":
            return left != right
        if op == "and":
            return left and right
        if op == "or":
            return left or right
        raise RuntimeError(f"Unknown operator: {op!r}")

    def _eval_unaryop(self, node: dict) -> Any:
        op = node["op"]
        val = self._eval(node["operand"])
        if op == "-":
            return -val
        if op == "not":
            return not val
        raise RuntimeError(f"Unknown unary operator: {op!r}")

    # ------------------------------------------------------------------
    # Subscript  (e.g. close[1])
    # ------------------------------------------------------------------

    def _eval_subscript(self, node: dict) -> Any:
        name = node["name"]
        idx = self._eval(node["index"])
        assert self._df is not None
        if name in self._df.columns:
            row_idx = -1 - int(idx)
            if abs(row_idx) > self._df.height:
                return 0.0
            return self._df[name][row_idx]
        raise RuntimeError(f"Unknown series: {name!r}")

    # ------------------------------------------------------------------
    # Function calls
    # ------------------------------------------------------------------

    def _eval_call(self, node: dict) -> Any:
        name: str = node["name"]
        raw_args = node["args"]

        # Collect positional and keyword arguments
        pos_args: list[Any] = []
        kw_args: dict[str, Any] = {}
        for a in raw_args:
            if a["type"] == "kwarg":
                kw_args[a["name"]] = self._eval(a["value"])
            else:
                pos_args.append(self._eval(a))

        # --- strategy() declaration ---
        if name == "strategy":
            self._strategy_declared = True
            return None

        # --- input.int / input.float / input.string ---
        if name.startswith("input."):
            defval = kw_args.get("defval", pos_args[0] if pos_args else 0)
            title = kw_args.get("title", "")
            # We just return the default value (no UI)
            return defval

        # --- ta.* indicators ---
        if name == "ta.sma":
            return self._ta_sma(pos_args, kw_args)
        if name == "ta.ema":
            return self._ta_ema(pos_args, kw_args)
        if name == "ta.rsi":
            return self._ta_rsi(pos_args, kw_args)
        if name == "ta.macd":
            return self._ta_macd(pos_args, kw_args)
        if name == "ta.crossover":
            return self._ta_crossover(pos_args)
        if name == "ta.crossunder":
            return self._ta_crossunder(pos_args)

        # --- strategy.entry / strategy.close ---
        if name == "strategy.entry":
            return self._strategy_entry(pos_args, kw_args)
        if name == "strategy.close":
            return self._strategy_close(pos_args, kw_args)

        raise RuntimeError(f"Unknown function: {name!r}")

    # ------------------------------------------------------------------
    # ta.* implementations
    # ------------------------------------------------------------------

    def _series(self, val: Any, col: str = "close") -> pl.Series:
        """Resolve a value to a Polars Series or scalar."""
        assert self._df is not None
        if isinstance(val, str) and val in self._df.columns:
            return self._df[val]
        if isinstance(val, (int, float)):
            return pl.Series([val] * self._df.height)
        if isinstance(val, pl.Series):
            return val
        # If it's a column name stored in a variable
        if isinstance(val, str):
            return self._df[val]
        return self._df[col]

    def _ta_sma(self, args: list, kw: dict) -> float:
        assert self._df is not None
        col = "close"
        period = args[1] if len(args) > 1 else kw.get("length", 20)
        series = self._df[col]
        if series.len() < period:
            return 0.0
        return series.tail(int(period)).mean()  # type: ignore[return-value]

    def _ta_ema(self, args: list, kw: dict) -> float:
        assert self._df is not None
        col = "close"
        period = args[1] if len(args) > 1 else kw.get("length", 20)
        series = self._df[col]
        if series.len() < period:
            return 0.0
        return series.ewm_mean(span=int(period))[-1]

    def _ta_rsi(self, args: list, kw: dict) -> float:
        assert self._df is not None
        col = "close"
        period = args[1] if len(args) > 1 else kw.get("length", 14)
        series = self._df[col]
        diff = series.diff()
        gain = diff.clip(lower_bound=0).tail(int(period)).mean()
        loss = (-diff.clip(upper_bound=0)).tail(int(period)).mean()
        if loss == 0:
            return 100.0
        rs = gain / loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _ta_macd(self, args: list, kw: dict) -> float:
        assert self._df is not None
        col = "close"
        fast = kw.get("fastlen", 12)
        slow = kw.get("slowlen", 26)
        series = self._df[col]
        fast_ema = series.ewm_mean(span=int(fast))[-1]
        slow_ema = series.ewm_mean(span=int(slow))[-1]
        return fast_ema - slow_ema

    def _ta_crossover(self, args: list) -> bool:
        """True when first series crosses above second."""
        assert self._df is not None and self._df.height >= 2
        a_curr, b_curr = args[0], args[1]
        # For scalar values from indicator calls on the current bar,
        # we need previous bar values.  As a simplification we check
        # the last two rows if the args are column names.
        if isinstance(a_curr, (int, float)) and isinstance(b_curr, (int, float)):
            # Cannot determine crossover from scalar values alone in a
            # single evaluation; the caller should have stored series.
            return False
        return False  # conservative – extended in strategy context

    def _ta_crossunder(self, args: list) -> bool:
        assert self._df is not None and self._df.height >= 2
        if isinstance(args[0], (int, float)) and isinstance(args[1], (int, float)):
            return False
        return False

    # ------------------------------------------------------------------
    # strategy.* calls
    # ------------------------------------------------------------------

    def _strategy_entry(self, args: list, kw: dict) -> None:
        entry_id = args[0] if args else kw.get("id", "entry")
        direction = args[1] if len(args) > 1 else kw.get("direction", "strategy.long")
        qty = kw.get("qty", 1)
        if isinstance(qty, float):
            qty = int(qty)

        if "long" in str(direction).lower():
            self.signals.append(Signal(type=SignalType.BUY, size=qty, reason=str(entry_id)))
        else:
            self.signals.append(Signal(type=SignalType.SELL, size=qty, reason=str(entry_id)))

    def _strategy_close(self, args: list, kw: dict) -> None:
        close_id = args[0] if args else kw.get("id", "close")
        if "long" in str(close_id).lower():
            self.signals.append(Signal(type=SignalType.CLOSE_LONG, reason=str(close_id)))
        elif "short" in str(close_id).lower():
            self.signals.append(Signal(type=SignalType.CLOSE_SHORT, reason=str(close_id)))
        else:
            # Generic close – close both sides
            self.signals.append(Signal(type=SignalType.CLOSE_LONG, reason=str(close_id)))
            self.signals.append(Signal(type=SignalType.CLOSE_SHORT, reason=str(close_id)))
