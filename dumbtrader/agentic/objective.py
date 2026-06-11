"""The reward function — frozen before trading, never moved after a result.

The headline score is **loop integrity**, not P&L:

- ``adherence`` — did we actually run the policy we're about to reflect on?
  A loop that reflects on a policy that was never run learns garbage.
- ``excess_return`` vs the laziest alternative (buy-and-hold the benchmark).
  If the loop can't beat buy-it-and-go-snowboarding, name that honestly.

Everything else (hit rate, avg win/loss, expectancy, max drawdown) is
diagnostic context for the reflection, not a goal to optimize directly.
"""

from __future__ import annotations

from dataclasses import dataclass

from dumbtrader.agentic.guardrails import max_drawdown


@dataclass
class ClosedTrade:
    """A round-trip used for scoring."""

    symbol: str
    pnl: float  # realized dollar P&L (net of fees/slippage if known)
    followed_policy: bool  # was this trade taken per the policy (not an override)?


@dataclass
class Metrics:
    """The scorecard for one rollout. The objective is frozen; this is output."""

    n_trades: int
    n_followed: int
    n_overrides: int
    adherence: float  # fraction of decisions that followed the policy
    hit_rate: float
    avg_win: float
    avg_loss: float
    win_loss_ratio: float
    expectancy: float  # average P&L per trade
    total_pnl: float
    return_pct: float  # account return over the window
    benchmark_return_pct: float  # buy-and-hold the benchmark, same window
    excess_return_pct: float  # return_pct - benchmark_return_pct
    max_drawdown: float  # peak-to-trough on the account equity curve

    def report(self) -> str:
        beat = "BEAT" if self.excess_return_pct > 0 else "LOST TO"
        return (
            "Scorecard (objective is loop integrity, not P&L)\n"
            f"  adherence       : {self.adherence:.0%}  "
            f"({self.n_followed} followed / {self.n_overrides} overrides)\n"
            f"  trades          : {self.n_trades}\n"
            f"  hit rate        : {self.hit_rate:.0%}\n"
            f"  avg win / loss  : ${self.avg_win:.2f} / ${self.avg_loss:.2f}  "
            f"(W/L {self.win_loss_ratio:.2f})\n"
            f"  expectancy      : ${self.expectancy:.2f}/trade\n"
            f"  total P&L       : ${self.total_pnl:.2f}\n"
            f"  return          : {self.return_pct:+.2%}\n"
            f"  benchmark       : {self.benchmark_return_pct:+.2%}\n"
            f"  excess          : {self.excess_return_pct:+.2%}  "
            f"({beat} buy-and-hold)\n"
            f"  max drawdown    : {self.max_drawdown:.2%}"
        )


def score_rollout(
    trades: list[ClosedTrade],
    *,
    equity_curve: list[float],
    benchmark_return_pct: float,
) -> Metrics:
    """Compute the frozen scorecard for a rollout.

    ``equity_curve`` is the account equity sampled over the window (first =
    start, last = end); it drives both return and drawdown so the two can't
    disagree.  ``benchmark_return_pct`` is buy-and-hold of the benchmark over
    the same window, computed by the caller from quotes.
    """
    n = len(trades)
    n_followed = sum(1 for t in trades if t.followed_policy)
    n_overrides = n - n_followed
    adherence = (n_followed / n) if n else 1.0

    wins = [t.pnl for t in trades if t.pnl > 0]
    losses = [t.pnl for t in trades if t.pnl < 0]
    total_pnl = sum(t.pnl for t in trades)

    hit_rate = (len(wins) / n) if n else 0.0
    avg_win = (sum(wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(losses) / len(losses)) if losses else 0.0  # negative
    win_loss_ratio = (avg_win / abs(avg_loss)) if avg_loss else 0.0
    expectancy = (total_pnl / n) if n else 0.0

    if equity_curve and equity_curve[0] > 0:
        return_pct = (equity_curve[-1] - equity_curve[0]) / equity_curve[0]
    else:
        return_pct = 0.0

    return Metrics(
        n_trades=n,
        n_followed=n_followed,
        n_overrides=n_overrides,
        adherence=adherence,
        hit_rate=hit_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        win_loss_ratio=win_loss_ratio,
        expectancy=expectancy,
        total_pnl=total_pnl,
        return_pct=return_pct,
        benchmark_return_pct=benchmark_return_pct,
        excess_return_pct=return_pct - benchmark_return_pct,
        max_drawdown=max_drawdown(equity_curve),
    )
