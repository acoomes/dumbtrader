"""Operator CLI for the agentic loop: ``dumbtrader-loop``.

This is the human surface for the weekly ritual. It mirrors the Morning
Download pattern: load state, propose under the policy, surface a review,
confirm, record, reflect. Real order placement happens through the operator's
broker tools *after* confirmation — this CLI's built-in executor only records a
dry-run fill so the mechanics can be exercised end-to-end without touching a
live account.

Subcommands
-----------
  status   show the active playbook, latest iteration, adherence, lessons
  propose  apply the policy to a market-snapshot JSON and review/confirm
  reflect  render the post-mortem template for an iteration
  gate     evaluate a pre-registered policy-change proposal
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from dumbtrader.agentic.gate import PolicyChangeProposal, evaluate_gate
from dumbtrader.agentic.ledger import Decision, LoopLedger, Rollout
from dumbtrader.agentic.playbook import load_playbook
from dumbtrader.agentic.reflection import build_reflection
from dumbtrader.agentic.objective import ClosedTrade, score_rollout
from dumbtrader.agentic.rollout import (
    MarketSnapshot,
    execute_confirmed,
    propose_weekly,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_PLAYBOOK = _REPO_ROOT / "agentic" / "playbook" / "v0.md"
_ITER_DIR = _REPO_ROOT / "agentic" / "iterations"

logger = logging.getLogger("dumbtrader.agentic.cli")


def _interactive_confirm(decision: Decision) -> bool:
    print(
        f"\nPROPOSED: {decision.action} {decision.proposed_shares:.4f} "
        f"{decision.symbol}  (~${decision.proposed_notional:.2f})"
    )
    print(f"  reason: {decision.rationale}")
    ans = input("  place this order? [y/N] ").strip().lower()
    return ans in ("y", "yes")


def _dry_run_executor(decision: Decision) -> float | None:
    """Records a dry-run fill at the proposed notional/shares price."""
    if decision.proposed_shares <= 0:
        return None
    return decision.proposed_notional / decision.proposed_shares


def cmd_status(args: argparse.Namespace) -> int:
    pb = load_playbook(args.playbook)
    ledger = LoopLedger()
    try:
        it = ledger.latest_iteration()
        print(pb.summary())
        print(f"objective: {pb.objective}")
        print(f"latest iteration: {it}")
        print(f"overall adherence: {ledger.adherence():.0%}")
        print(f"lessons distilled: {len(ledger.lessons())}")
        print(f"closed decisions (this version): "
              f"{len(ledger.closed_decisions(pb.version))}")
    finally:
        ledger.close()
    return 0


def cmd_propose(args: argparse.Namespace) -> int:
    pb = load_playbook(args.playbook)
    raw = json.loads(Path(args.snapshots).read_text())
    snapshots = [MarketSnapshot(**s) for s in raw["symbols"]]
    equity = float(raw["equity"])
    regime = raw.get("regime", "untagged")
    open_positions = sum(1 for s in snapshots if s.held_shares > 0)

    ledger = LoopLedger()
    try:
        iteration = ledger.latest_iteration() + 1
        decisions = propose_weekly(
            pb, snapshots, equity=equity, iteration=iteration,
            regime=regime, open_positions=open_positions,
        )
        if not decisions:
            print("Policy proposes HOLD this week. Nothing to confirm.")
            return 0

        print(f"\n{len(decisions)} proposed decision(s) for iteration {iteration}:")

        if not args.execute:
            # Dry preview: do NOT persist a rollout or advance the iteration
            # counter — only `--execute` commits state through the human gate.
            for d in decisions:
                print(f"  - {d.action} {d.proposed_shares:.4f} {d.symbol} "
                      f"(~${d.proposed_notional:.2f}): {d.rationale}")
            print("\n(dry preview — nothing persisted; re-run with --execute to "
                  "record under the human gate)")
            return 0

        # Execute path: persist the rollout, then drive decisions through the gate.
        ledger.start_rollout(Rollout(
            iteration=iteration,
            policy_version=pb.version,
            started_at=datetime.now(timezone.utc).isoformat(),
            window_label=raw.get("window_label", f"iter-{iteration}"),
            benchmark=pb.benchmark,
            equity_start=equity,
            regime=regime,
        ))
        confirm = (lambda d: True) if args.yes else _interactive_confirm
        results = execute_confirmed(
            decisions, ledger=ledger, confirm=confirm,
            execute=_dry_run_executor,
        )
        placed = sum(1 for d in results if d.status == "executed")
        print(f"\n{placed}/{len(results)} executed (dry-run fills recorded).")
    finally:
        ledger.close()
    return 0


def cmd_reflect(args: argparse.Namespace) -> int:
    pb = load_playbook(args.playbook)
    ledger = LoopLedger()
    try:
        iteration = args.iteration or ledger.latest_iteration()
        decisions = ledger.decisions_for(iteration)
        closed = [
            ClosedTrade(symbol=d.symbol, pnl=d.pnl or 0.0,
                        followed_policy=bool(d.followed_policy))
            for d in decisions if d.pnl is not None
        ]
        metrics = score_rollout(
            closed,
            equity_curve=[args.equity_start, args.equity_end],
            benchmark_return_pct=args.benchmark_return,
        )
        regime = decisions[0].regime if decisions else "untagged"
        text = build_reflection(
            iteration=iteration, policy_version=pb.version, regime=regime,
            metrics=metrics, decisions=decisions,
        )
    finally:
        ledger.close()

    _ITER_DIR.mkdir(parents=True, exist_ok=True)
    out = _ITER_DIR / f"iteration-{iteration:03d}.md"
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"\nWrote {out}")
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    pb = load_playbook(args.playbook)
    proposal = PolicyChangeProposal(
        hypothesis=args.hypothesis,
        rule_target=args.rule,
        regime=args.regime,
        required_n=pb.gate.min_decisions,
    )
    result = evaluate_gate(
        proposal,
        observed_decisions=args.observed,
        successes=args.successes,
        baseline_rate=args.baseline,
    )
    print(result.reason)
    return 0 if result.allowed else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dumbtrader-loop",
        description="Agentic self-improvement trading loop (human-gated, weekly)",
    )
    p.add_argument("--playbook", default=str(_DEFAULT_PLAYBOOK),
                   help="path to the playbook markdown (default: agentic/playbook/v0.md)")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="show active playbook and loop state")

    pp = sub.add_parser("propose", help="apply the policy to a snapshot and review")
    pp.add_argument("snapshots", help="path to a market-snapshot JSON file")
    pp.add_argument("--execute", action="store_true",
                    help="record decisions through the human gate (dry-run fills)")
    pp.add_argument("--yes", action="store_true",
                    help="auto-confirm (testing only; bypasses the interactive gate)")

    pr = sub.add_parser("reflect", help="render the post-mortem for an iteration")
    pr.add_argument("--iteration", type=int, default=0,
                    help="iteration number (default: latest)")
    pr.add_argument("--equity-start", type=float, required=True)
    pr.add_argument("--equity-end", type=float, required=True)
    pr.add_argument("--benchmark-return", type=float, required=True,
                    help="buy-and-hold benchmark return over the window (fraction)")

    pg = sub.add_parser("gate", help="evaluate a pre-registered policy change")
    pg.add_argument("--hypothesis", required=True)
    pg.add_argument("--rule", required=True, help="rule target, e.g. exit.stop_pct")
    pg.add_argument("--regime", default="untagged")
    pg.add_argument("--observed", type=int, required=True,
                    help="decisions of evidence gathered")
    pg.add_argument("--successes", type=int, default=None)
    pg.add_argument("--baseline", type=float, default=None)
    return p


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    parser = build_parser()
    args = parser.parse_args()
    handlers = {
        "status": cmd_status,
        "propose": cmd_propose,
        "reflect": cmd_reflect,
        "gate": cmd_gate,
    }
    raise SystemExit(handlers[args.command](args))


if __name__ == "__main__":
    main()
