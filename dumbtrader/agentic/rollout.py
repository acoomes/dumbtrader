"""The weekly rollout: propose -> review -> confirm -> execute -> record.

This module turns a :class:`~dumbtrader.agentic.playbook.Playbook` plus a
market snapshot into a list of *proposed* decisions, then drives them through a
mandatory human gate before anything is executed.

The human gate is structural, not optional: :func:`execute_confirmed` will only
act on decisions a ``Confirmer`` has approved, and the actual order placement
is delegated to an injected ``Executor``.  Library code never calls a broker
API directly — the seam is where the operator (or the agent, via the Robinhood
tools, after confirmation) plugs in.  There is no auto-firing path.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from dumbtrader.agentic.guardrails import PositionSizer, within_position_cap
from dumbtrader.agentic.ledger import (
    CONFIRMED,
    EXECUTED,
    PROPOSED,
    REJECTED,
    SKIPPED,
    Decision,
    LoopLedger,
)
from dumbtrader.agentic.playbook import Playbook

logger = logging.getLogger("dumbtrader.agentic.rollout")


@dataclass
class MarketSnapshot:
    """The per-symbol features the v0 systematic-swing policy needs.

    The caller computes these from quotes/history (e.g. the Robinhood tools);
    keeping them as plain inputs makes the policy logic pure and testable.
    """

    symbol: str
    price: float
    sma: float  # trend filter, e.g. 20-week SMA
    momentum: float  # ranking signal, e.g. 12-week return (fraction)
    held_shares: float = 0.0  # currently held (for exit evaluation)
    entry_price: float | None = None  # cost basis of a held position (for the stop)


class Executor(Protocol):
    """Places a confirmed order and returns the fill price (or None on fail)."""

    def __call__(self, decision: Decision) -> float | None: ...


class Confirmer(Protocol):
    """The human gate. Returns True to place the order, False to skip it."""

    def __call__(self, decision: Decision) -> bool: ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def propose_weekly(
    playbook: Playbook,
    snapshots: list[MarketSnapshot],
    *,
    equity: float,
    iteration: int,
    regime: str,
    open_positions: int = 0,
) -> list[Decision]:
    """Apply the v0 systematic-swing rules to produce proposed decisions.

    v0 rules (deliberately dumb, explicit, falsifiable):
      - **Exit:** close any held name whose price has closed below its SMA OR
        breached the ``exit.stop_pct`` stop from its entry price (the playbook
        exit rule is "below-SMA OR -10% stop, whichever comes first").
      - **Entry:** among names with price > SMA and momentum > 0, take the
        highest-momentum name, sized by the risk-fraction rule, up to the
        max-positions cap.

    Everything returned is ``status=proposed`` — nothing is executed here.
    """
    sizer = PositionSizer(playbook.sizing)
    decisions: list[Decision] = []
    held_after = open_positions
    stop_pct = float(playbook.exit.get("stop_pct", 0.0))

    # 1) Exits first (free up the position cap before considering entries).
    for snap in snapshots:
        if snap.held_shares <= 0:
            continue
        below_sma = snap.price < snap.sma
        stop_hit = (
            stop_pct > 0
            and snap.entry_price is not None
            and snap.price <= snap.entry_price * (1 - stop_pct)
        )
        if not (below_sma or stop_hit):
            continue

        if stop_hit and not below_sma:
            reason = (
                f"exit: price {snap.price:.2f} hit -{stop_pct:.0%} stop from "
                f"entry {snap.entry_price:.2f} (loss cap)"
            )
        elif below_sma and stop_hit:
            reason = (
                f"exit: price {snap.price:.2f} < SMA {snap.sma:.2f} and below "
                f"-{stop_pct:.0%} stop"
            )
        else:
            reason = (
                f"exit: price {snap.price:.2f} < SMA {snap.sma:.2f} "
                f"(trend filter broken)"
            )

        decisions.append(
            Decision(
                iteration=iteration,
                policy_version=playbook.version,
                timestamp=_now(),
                symbol=snap.symbol,
                action="CLOSE",
                proposed_shares=snap.held_shares,
                proposed_notional=snap.held_shares * snap.price,
                rationale=reason,
                regime=regime,
                status=PROPOSED,
            )
        )
        held_after -= 1

    # 2) Entries: rank eligible names by momentum, take the strongest until the
    #    position cap is full.
    eligible = sorted(
        (s for s in snapshots if s.held_shares == 0
         and s.price > s.sma and s.momentum > 0),
        key=lambda s: s.momentum,
        reverse=True,
    )
    for snap in eligible:
        if held_after >= playbook.sizing.max_positions:
            break
        sized = sizer.size(symbol=snap.symbol, equity=equity, price=snap.price)
        decisions.append(
            Decision(
                iteration=iteration,
                policy_version=playbook.version,
                timestamp=_now(),
                symbol=snap.symbol,
                action="BUY",
                proposed_shares=sized.shares,
                proposed_notional=sized.notional,
                rationale=(
                    f"entry: price {snap.price:.2f} > SMA {snap.sma:.2f}, "
                    f"momentum {snap.momentum:+.1%} (top eligible); "
                    f"sized by {sized.capped_by}"
                ),
                regime=regime,
                status=PROPOSED,
            )
        )
        held_after += 1

    if not decisions:
        logger.info("Iteration %d: policy proposes HOLD (no eligible action)", iteration)
    return decisions


def execute_confirmed(
    decisions: list[Decision],
    *,
    ledger: LoopLedger,
    confirm: Confirmer,
    execute: Executor,
) -> list[Decision]:
    """Drive proposed decisions through the human gate, then execute approved ones.

    For each decision: record it (proposed), ask ``confirm``; if approved,
    mark confirmed and call ``execute`` to place the order, recording the fill;
    if declined, mark it rejected AND flag it as an override (declining the
    policy's proposal is a deviation from the policy as written).  Every
    outcome is persisted so the reflection scores the policy *as actually run*.
    """
    results: list[Decision] = []
    for d in decisions:
        d.id = ledger.record_decision(d)

        approved = confirm(d)
        if not approved:
            # Declining a proposal IS a deviation from the policy as written:
            # record it as an override so the adherence audit reflects the
            # policy *as actually run*, not 100% by default.
            override = "declined at human gate"
            ledger.update_decision(
                d.id, status=REJECTED, followed_policy=0, override_reason=override
            )
            d.status = REJECTED
            d.followed_policy = 0
            d.override_reason = override
            logger.info("Declined (override): %s %s", d.action, d.symbol)
            results.append(d)
            continue

        ledger.update_decision(d.id, status=CONFIRMED)
        d.status = CONFIRMED

        fill = execute(d)
        if fill is None:
            ledger.update_decision(d.id, status=SKIPPED)
            d.status = SKIPPED
            logger.warning("Execution failed/skipped: %s %s", d.action, d.symbol)
        else:
            ledger.update_decision(
                d.id,
                status=EXECUTED,
                fill_price=fill,
                executed_shares=d.proposed_shares,
            )
            d.status = EXECUTED
            d.fill_price = fill
            d.executed_shares = d.proposed_shares
            logger.info("Executed: %s %s @ %.4f", d.action, d.symbol, fill)
        results.append(d)
    return results
