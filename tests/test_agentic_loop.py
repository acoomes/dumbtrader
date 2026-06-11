"""Tests for the agentic loop's pure logic: objective, guardrails, gate,
playbook loading, and proposal generation.

Run with: ``pytest tests/test_agentic_loop.py`` (or ``python -m pytest``).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from dumbtrader.agentic.gate import PolicyChangeProposal, evaluate_gate
from dumbtrader.agentic.guardrails import (
    PositionSizer,
    kill_switch_tripped,
    max_drawdown,
)
from dumbtrader.agentic.ledger import Decision, LoopLedger, Rollout
from dumbtrader.agentic.objective import ClosedTrade, score_rollout
from dumbtrader.agentic.playbook import SizingRule, load_playbook
from dumbtrader.agentic.rollout import (
    MarketSnapshot,
    execute_confirmed,
    propose_weekly,
)

_V0 = Path(__file__).resolve().parents[1] / "agentic" / "playbook" / "v0.md"


# --------------------------------------------------------------------------
# playbook
# --------------------------------------------------------------------------

def test_v0_playbook_loads_and_validates():
    pb = load_playbook(_V0)
    assert pb.version == "v0"
    assert pb.cadence == "weekly"
    assert pb.benchmark == "SPY"
    assert pb.sizing.max_positions == 1
    assert pb.sizing.risk_fraction == 0.08
    assert pb.gate.min_decisions == 20
    assert "Systematic Equity Swing" in pb.body


def test_sizing_rule_rejects_oversized_risk():
    with pytest.raises(ValueError):
        SizingRule(risk_fraction=0.5, assumed_stop_pct=0.1, max_positions=1)


# --------------------------------------------------------------------------
# objective / scoring
# --------------------------------------------------------------------------

def test_score_rollout_basic_metrics():
    trades = [
        ClosedTrade("AAPL", 10.0, followed_policy=True),
        ClosedTrade("MSFT", -4.0, followed_policy=True),
        ClosedTrade("NVDA", 6.0, followed_policy=False),  # an override
    ]
    m = score_rollout(trades, equity_curve=[230, 242], benchmark_return_pct=0.01)
    assert m.n_trades == 3
    assert m.n_overrides == 1
    assert m.adherence == pytest.approx(2 / 3)
    assert m.hit_rate == pytest.approx(2 / 3)
    assert m.total_pnl == pytest.approx(12.0)
    assert m.avg_win == pytest.approx(8.0)
    assert m.avg_loss == pytest.approx(-4.0)
    assert m.win_loss_ratio == pytest.approx(2.0)
    assert m.return_pct == pytest.approx((242 - 230) / 230)
    assert m.excess_return_pct == pytest.approx(m.return_pct - 0.01)


def test_score_rollout_empty_is_full_adherence():
    m = score_rollout([], equity_curve=[230, 230], benchmark_return_pct=0.0)
    assert m.n_trades == 0
    assert m.adherence == 1.0
    assert m.return_pct == 0.0


# --------------------------------------------------------------------------
# guardrails
# --------------------------------------------------------------------------

def test_max_drawdown():
    assert max_drawdown([100, 120, 90, 110]) == pytest.approx(0.25)
    assert max_drawdown([100, 110, 120]) == 0.0
    assert max_drawdown([]) == 0.0


def test_kill_switch_trips_at_threshold():
    assert kill_switch_tripped([100, 70], 0.30) is True
    assert kill_switch_tripped([100, 75], 0.30) is False


def test_position_sizer_risk_fraction_binds():
    # max_positions=1 -> equal-weight ceiling is full equity, so risk binds.
    sizer = PositionSizer(SizingRule(0.08, 0.10, max_positions=1))
    d = sizer.size(symbol="NVDA", equity=230.0, price=100.0)
    # notional = 0.08 * 230 / 0.10 = 184
    assert d.notional == pytest.approx(184.0)
    assert d.shares == pytest.approx(1.84)
    assert "risk_fraction" in d.capped_by


def test_position_sizer_equal_weight_binds_with_many_positions():
    sizer = PositionSizer(SizingRule(0.08, 0.10, max_positions=5))
    d = sizer.size(symbol="NVDA", equity=230.0, price=100.0)
    # equal-weight ceiling = 230/5 = 46 < risk notional 184
    assert d.notional == pytest.approx(46.0)
    assert "max_positions" in d.capped_by


# --------------------------------------------------------------------------
# gate
# --------------------------------------------------------------------------

def test_gate_blocks_insufficient_evidence():
    p = PolicyChangeProposal("tighter stop", "exit.stop_pct", "chop", required_n=20)
    res = evaluate_gate(p, observed_decisions=5)
    assert not res
    assert "BLOCKED" in res.reason


def test_gate_clears_on_sample_size():
    p = PolicyChangeProposal("tighter stop", "exit.stop_pct", "chop", required_n=20)
    res = evaluate_gate(p, observed_decisions=25)
    assert res
    assert "cleared" in res.reason


def test_gate_clears_on_significance():
    p = PolicyChangeProposal("higher hit rate", "entry.min_momentum", "bull", required_n=50)
    # 18/20 successes vs 50% baseline is strongly significant
    res = evaluate_gate(p, observed_decisions=20, successes=18, baseline_rate=0.5)
    assert res
    assert "stat-sig" in res.reason


# --------------------------------------------------------------------------
# rollout proposal logic
# --------------------------------------------------------------------------

def test_propose_weekly_picks_highest_momentum_above_sma():
    pb = load_playbook(_V0)
    snaps = [
        MarketSnapshot("AAPL", price=212.5, sma=205, momentum=0.06),
        MarketSnapshot("NVDA", price=135, sma=120, momentum=0.18),
        MarketSnapshot("GOOGL", price=178, sma=182, momentum=-0.02),  # below SMA
    ]
    decisions = propose_weekly(
        pb, snaps, equity=230.0, iteration=1, regime="melt-up"
    )
    # max_positions=1 -> exactly one BUY, the highest-momentum eligible name
    buys = [d for d in decisions if d.action == "BUY"]
    assert len(buys) == 1
    assert buys[0].symbol == "NVDA"


def test_propose_weekly_exits_when_below_sma():
    pb = load_playbook(_V0)
    snaps = [
        MarketSnapshot("AAPL", price=200, sma=210, momentum=0.01, held_shares=1.0),
    ]
    decisions = propose_weekly(
        pb, snaps, equity=230.0, iteration=2, regime="chop", open_positions=1
    )
    assert any(d.action == "CLOSE" and d.symbol == "AAPL" for d in decisions)


def test_propose_weekly_exits_on_stop_even_when_above_sma():
    # Held position still above its SMA but down >10% from entry -> the stop
    # exit (loss cap) must fire; otherwise we'd hold past the stated cap.
    pb = load_playbook(_V0)  # exit.stop_pct == 0.10
    snaps = [
        MarketSnapshot("AAPL", price=180, sma=170, momentum=0.01,
                       held_shares=1.0, entry_price=205.0),  # -12% from entry
    ]
    decisions = propose_weekly(
        pb, snaps, equity=230.0, iteration=4, regime="chop", open_positions=1
    )
    closes = [d for d in decisions if d.action == "CLOSE" and d.symbol == "AAPL"]
    assert len(closes) == 1
    assert "stop" in closes[0].rationale


def test_propose_weekly_holds_position_within_stop_and_above_sma():
    pb = load_playbook(_V0)
    snaps = [
        MarketSnapshot("AAPL", price=200, sma=170, momentum=0.01,
                       held_shares=1.0, entry_price=205.0),  # only -2.4%
    ]
    decisions = propose_weekly(
        pb, snaps, equity=230.0, iteration=5, regime="bull", open_positions=1
    )
    assert all(d.action != "CLOSE" for d in decisions)


def test_execute_confirmed_declined_counts_as_override():
    pb = load_playbook(_V0)
    snaps = [MarketSnapshot("NVDA", price=135, sma=120, momentum=0.18)]
    decisions = propose_weekly(pb, snaps, equity=230.0, iteration=1, regime="bull")
    assert decisions

    with tempfile.TemporaryDirectory() as tmp:
        ledger = LoopLedger(Path(tmp) / "loop.db")
        try:
            results = execute_confirmed(
                decisions, ledger=ledger,
                confirm=lambda d: False,  # operator declines at the gate
                execute=lambda d: pytest.fail("must not execute a declined order"),
            )
            assert results[0].status == "rejected"
            assert results[0].followed_policy == 0
            # The adherence audit must reflect the decline, not report 100%.
            assert ledger.adherence(1) == 0.0
        finally:
            ledger.close()


def test_propose_weekly_holds_when_nothing_eligible():
    pb = load_playbook(_V0)
    snaps = [
        MarketSnapshot("AAPL", price=200, sma=210, momentum=-0.05),  # below SMA
    ]
    decisions = propose_weekly(pb, snaps, equity=230.0, iteration=3, regime="bear")
    assert decisions == []


# --------------------------------------------------------------------------
# ledger (round-trip on a temp db)
# --------------------------------------------------------------------------

def test_ledger_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "loop.db"
        ledger = LoopLedger(db)
        try:
            assert ledger.latest_iteration() == 0
            ledger.start_rollout(Rollout(
                iteration=1, policy_version="v0",
                started_at="2026-06-11T00:00:00+00:00",
                window_label="2026-W24", benchmark="SPY", equity_start=230.0,
            ))
            assert ledger.latest_iteration() == 1

            d = Decision(
                iteration=1, policy_version="v0", timestamp="",
                symbol="NVDA", action="BUY", proposed_shares=1.8,
                proposed_notional=184.0, rationale="top momentum",
                regime="melt-up",
            )
            did = ledger.record_decision(d)
            ledger.update_decision(did, status="executed", fill_price=100.0,
                                   executed_shares=1.8, pnl=12.0)

            closed = ledger.closed_decisions("v0")
            assert len(closed) == 1
            assert closed[0].pnl == 12.0
            assert ledger.adherence(1) == 1.0
        finally:
            ledger.close()
