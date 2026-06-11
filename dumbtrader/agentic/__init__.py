"""Agentic self-improvement trading loop (Karpathy-style in-context loop).

This subsystem is intentionally *separate* from the async ProjectX futures
engine in :mod:`dumbtrader.engine`.  It implements a human-gated, weekly
decision loop whose product is not P&L but a versioned, falsifiable
**playbook** — the "weights" of an in-context self-improvement loop:

    act  ->  score  ->  reflect  ->  distill a lesson into the playbook  ->  repeat

The dollars are test fixtures; the score is *loop integrity*, not P&L.

Components
----------
- :mod:`dumbtrader.agentic.playbook`     the versioned policy ("weights")
- :mod:`dumbtrader.agentic.objective`    the frozen reward / scoring function
- :mod:`dumbtrader.agentic.guardrails`   position sizing, kill switch, regimes
- :mod:`dumbtrader.agentic.gate`         the sample-size gate on policy changes
- :mod:`dumbtrader.agentic.ledger`       decision / adherence / lesson ledger
- :mod:`dumbtrader.agentic.reflection`   structured post-mortem template
- :mod:`dumbtrader.agentic.rollout`      weekly propose -> confirm -> execute

Design invariants (non-negotiable, see ``agentic/docs/DESIGN.md``):
- **Human gate.** Every order is proposed, surfaced for review, and only
  placed after explicit confirmation. There is no auto-firing path.
- **Frozen objective.** The reward function is defined before trading and is
  not edited after seeing a result.
- **Sample-size gate.** No rule change without N pre-registered decisions of
  evidence.
"""

from __future__ import annotations

from dumbtrader.agentic.gate import GateResult, PolicyChangeProposal, evaluate_gate
from dumbtrader.agentic.guardrails import (
    PositionSizer,
    SizeDecision,
    kill_switch_tripped,
    max_drawdown,
)
from dumbtrader.agentic.ledger import Decision, Lesson, LoopLedger, Rollout
from dumbtrader.agentic.objective import Metrics, score_rollout
from dumbtrader.agentic.playbook import Playbook, load_playbook

__all__ = [
    "Decision",
    "GateResult",
    "Lesson",
    "LoopLedger",
    "Metrics",
    "Playbook",
    "PolicyChangeProposal",
    "PositionSizer",
    "Rollout",
    "SizeDecision",
    "evaluate_gate",
    "kill_switch_tripped",
    "load_playbook",
    "max_drawdown",
    "score_rollout",
]
