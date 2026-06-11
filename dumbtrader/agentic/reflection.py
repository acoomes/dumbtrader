"""Structured post-mortem (the 'reflect' step).

Reflection attributes outcomes to *decisions*, not results.  On small samples a
great decision loses and a terrible one wins; outcome-bias will have you
"learning" from variance.  The template below forces a decision-quality grade
that is recorded separately from the P&L, and it is the designated plug-in
point for a stochastic-multi-agent-consensus panel so a single perspective's
hindsight bias doesn't get baked into the playbook.
"""

from __future__ import annotations

from dumbtrader.agentic.ledger import Decision
from dumbtrader.agentic.objective import Metrics


def _decision_line(d: Decision) -> str:
    pnl = "n/a" if d.pnl is None else f"${d.pnl:+.2f}"
    adher = "followed" if d.followed_policy else f"OVERRIDE ({d.override_reason})"
    return (
        f"- **{d.symbol} {d.action}** ({d.status}, {adher}) — {pnl}\n"
        f"  - rule: {d.rationale}\n"
        f"  - regime: {d.regime}\n"
        f"  - decision quality (not outcome): ____ / 5  — why: \n"
    )


def build_reflection(
    *,
    iteration: int,
    policy_version: str,
    regime: str,
    metrics: Metrics,
    decisions: list[Decision],
) -> str:
    """Render a markdown post-mortem template for one iteration.

    The numbers are filled in; the *judgements* (decision-quality grades, the
    one lesson, the pre-registered hypothesis) are left blank for the human /
    consensus panel to complete — the loop must not auto-grade its own work.
    """
    overrides = [d for d in decisions if not d.followed_policy]
    lines: list[str] = []
    lines.append(f"# Reflection — iteration {iteration} (policy {policy_version})")
    lines.append("")
    lines.append(f"_Regime tag: **{regime}**_  (a lesson learned in a melt-up is")
    lines.append("a liability in a chop — stamp every lesson with its regime.)")
    lines.append("")
    lines.append("## 1. Scorecard")
    lines.append("```")
    lines.append(metrics.report())
    lines.append("```")
    lines.append("")
    lines.append("## 2. Adherence audit")
    if overrides:
        lines.append(
            f"{len(overrides)} override(s) this iteration. The reflection below "
            "scores the policy *as actually run*, including these:"
        )
        for d in overrides:
            lines.append(f"  - {d.symbol} {d.action}: {d.override_reason}")
    else:
        lines.append("No overrides — the policy was run as written. Good.")
    lines.append("")
    lines.append("## 3. Decision-by-decision (grade the decision, not the result)")
    if decisions:
        lines.extend(_decision_line(d) for d in decisions)
    else:
        lines.append("- (no decisions this iteration — HOLD week)")
    lines.append("")
    lines.append("## 4. Consensus panel (optional)")
    lines.append(
        "Run this post-mortem through the stochastic-multi-agent-consensus "
        "panel. Record where the panel *disagreed* — divergence is the signal "
        "that an outcome is being over-attributed to skill or luck."
    )
    lines.append("")
    lines.append("- Panel consensus: ")
    lines.append("- Notable dissent: ")
    lines.append("")
    lines.append("## 5. One lesson (candidate)")
    lines.append(
        "State at most ONE falsifiable lesson. It does NOT become policy yet — "
        "it becomes a pre-registered hypothesis that the sample-size gate must "
        "clear before the playbook changes."
    )
    lines.append("")
    lines.append("- Hypothesis: ")
    lines.append("- Rule it would change (e.g. `exit.stop_pct`): ")
    lines.append("- Evidence required (decisions): ")
    lines.append("- Benchmark honesty check: did the loop beat buy-and-hold? "
                 "If not, say so plainly.")
    lines.append("")
    return "\n".join(lines)
