"""Survival guardrails: position sizing, drawdown, kill switch.

You cannot run a loop on a dead account.  These functions cap per-trade risk
so a losing streak cannot zero the testbed before the loop learns anything,
and they define the hard halt (kill switch) where you stop tweaking and do a
full review instead.
"""

from __future__ import annotations

from dataclasses import dataclass

from dumbtrader.agentic.playbook import Playbook, SizingRule


@dataclass
class SizeDecision:
    """The sizing result for one proposed position."""

    symbol: str
    equity: float
    price: float
    risk_dollars: float  # capital put at risk to the stop
    notional: float  # dollar size of the position
    shares: float  # fractional shares (Robinhood supports fractional)
    capped_by: str  # which constraint bound the size

    def describe(self) -> str:
        return (
            f"{self.symbol}: {self.shares:.4f} sh  "
            f"(${self.notional:.2f} notional, risk ${self.risk_dollars:.2f}, "
            f"bound by {self.capped_by})"
        )


class PositionSizer:
    """Sizes positions to a fixed fraction of account risk.

    Given an assumed stop distance, risking ``risk_fraction`` of equity caps
    the *notional* at ``risk_fraction * equity / stop_pct``.  We also never let
    a single position exceed the per-position notional implied by the
    max-positions cap (equal-weight the account across the cap).
    """

    def __init__(self, sizing: SizingRule) -> None:
        self._s = sizing

    def size(self, *, symbol: str, equity: float, price: float) -> SizeDecision:
        if equity <= 0:
            raise ValueError("equity must be positive")
        if price <= 0:
            raise ValueError("price must be positive")

        stop_pct = self._s.assumed_stop_pct
        risk_dollars = self._s.risk_fraction * equity
        risk_notional = risk_dollars / stop_pct

        # Equal-weight ceiling so one name can't swallow the whole account
        # when the cap allows several positions.
        equal_weight_notional = equity / self._s.max_positions

        if risk_notional <= equal_weight_notional:
            notional = risk_notional
            capped_by = f"risk_fraction({self._s.risk_fraction:.0%})"
        else:
            notional = equal_weight_notional
            capped_by = f"max_positions({self._s.max_positions})"

        # Never size above available equity.
        if notional > equity:
            notional = equity
            capped_by = "available_equity"

        shares = notional / price
        return SizeDecision(
            symbol=symbol,
            equity=equity,
            price=price,
            risk_dollars=min(risk_dollars, notional * stop_pct),
            notional=notional,
            shares=shares,
            capped_by=capped_by,
        )


def max_drawdown(equity_curve: list[float]) -> float:
    """Return the maximum peak-to-trough drawdown as a positive fraction.

    ``[100, 120, 90, 110]`` -> 0.25 (120 -> 90).  Empty/flat curves -> 0.0.
    """
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    worst = 0.0
    for v in equity_curve:
        peak = max(peak, v)
        if peak > 0:
            dd = (peak - v) / peak
            worst = max(worst, dd)
    return worst


def kill_switch_tripped(equity_curve: list[float], threshold: float) -> bool:
    """True when drawdown from the running peak meets/exceeds ``threshold``.

    When this trips, the loop halts and the next iteration is a full review,
    not a rule tweak.
    """
    return max_drawdown(equity_curve) >= threshold


def within_position_cap(open_positions: int, playbook: Playbook) -> bool:
    """True if another position can be opened under the max-positions cap."""
    return open_positions < playbook.sizing.max_positions


def loss_within_cap(realized_loss_fraction: float, playbook: Playbook) -> bool:
    """True if a realized loss is within the per-trade loss cap.

    ``realized_loss_fraction`` is a positive fraction of account equity lost on
    the trade (0.05 == lost 5% of the account).
    """
    return realized_loss_fraction <= playbook.guardrails.max_loss_per_trade_fraction
