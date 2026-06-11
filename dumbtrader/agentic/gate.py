"""The sample-size gate — the one rule that separates a self-improvement loop
from a noise-amplifier.

No policy change without a pre-registered hypothesis and N decisions of
evidence.  A loop that "learns" from 5 trades is overfitting with confidence.
On small samples a great decision loses and a terrible one wins, so we gate
*every* rule rewrite on evidence, not on the last result.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class PolicyChangeProposal:
    """A pre-registered hypothesis for changing a playbook rule.

    Register this *before* collecting the evidence window, so you can't move
    the goalposts after seeing the outcome.
    """

    hypothesis: str  # falsifiable claim, e.g. "tightening the stop to 8% raises expectancy"
    rule_target: str  # which rule this would change, e.g. "exit.stop_pct"
    regime: str  # market regime the evidence was gathered in
    required_n: int  # minimum decisions of evidence (from playbook.gate.min_decisions)


@dataclass
class GateResult:
    allowed: bool
    reason: str

    def __bool__(self) -> bool:  # convenience: `if gate_result:`
        return self.allowed


def _proportion_significant(
    successes: int, n: int, baseline: float, alpha: float
) -> tuple[bool, float]:
    """One-sided normal-approx test that an observed rate beats ``baseline``.

    Returns ``(significant, z)``.  Used as an *alternative* satisfier of the
    gate: a statistically significant result can clear the gate even if the raw
    count is modest, matching "N decisions OR a stat-sig threshold".
    """
    if n <= 0:
        return False, 0.0
    p_hat = successes / n
    se = math.sqrt(baseline * (1 - baseline) / n)
    if se == 0:
        return False, 0.0
    z = (p_hat - baseline) / se
    # one-sided critical values for common alphas
    crit = {0.10: 1.2816, 0.05: 1.6449, 0.01: 2.3263}.get(alpha, 1.6449)
    return z >= crit, z


def evaluate_gate(
    proposal: PolicyChangeProposal,
    *,
    observed_decisions: int,
    successes: int | None = None,
    baseline_rate: float | None = None,
    alpha: float = 0.05,
) -> GateResult:
    """Decide whether a pre-registered policy change may be applied.

    The gate clears if EITHER:
      1. ``observed_decisions >= proposal.required_n`` (sample-size path), OR
      2. a one-sided test shows the observed success rate beats
         ``baseline_rate`` at ``alpha`` (stat-sig path), when ``successes`` and
         ``baseline_rate`` are supplied.

    Otherwise the change is blocked and the rule stays as-is for another
    iteration.
    """
    if observed_decisions >= proposal.required_n:
        return GateResult(
            True,
            f"sample-size gate cleared: {observed_decisions} >= "
            f"{proposal.required_n} decisions in regime '{proposal.regime}'",
        )

    if successes is not None and baseline_rate is not None:
        sig, z = _proportion_significant(
            successes, observed_decisions, baseline_rate, alpha
        )
        if sig:
            return GateResult(
                True,
                f"stat-sig gate cleared: rate {successes}/{observed_decisions} "
                f"beats baseline {baseline_rate:.0%} (z={z:.2f}, alpha={alpha})",
            )

    return GateResult(
        False,
        f"BLOCKED: only {observed_decisions}/{proposal.required_n} decisions of "
        f"evidence for '{proposal.hypothesis}'. Keep the rule; gather more. "
        f"Changing now is overfitting to variance.",
    )
