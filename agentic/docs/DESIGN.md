# Agentic Loop — Design

A Karpathy-style **in-context** self-improvement loop. Not gradient descent on
weights — the "weights" are a human-readable, versioned **playbook**, and the
update step is a git commit. The bottleneck at this account size isn't capital,
it's learning-signal quality, so the whole design optimizes for clean signal.

```
act  ->  score  ->  reflect  ->  distill a lesson into the playbook  ->  repeat
```

## North star

$230 will never make you rich. The asset being built is the **loop** — a
validated, portable decision policy that, once it survives contact with a real
market, gets pointed at dumbtrader or a funded account. The Agentic account is a
unit-test harness; the dollars are test fixtures. **Score loop integrity, not
P&L.**

## The five steps, mapped to code

| Step | Plan term | Module |
|------|-----------|--------|
| 1. Policy ("weights") | versioned playbook | `agentic/playbook/v0.md` + `dumbtrader/agentic/playbook.py` |
| 2. Rollout (act) | weekly decision window | `dumbtrader/agentic/rollout.py` |
| 3. Reward (score) | frozen objective vs benchmark | `dumbtrader/agentic/objective.py` |
| 4. Reflect (diagnose) | structured post-mortem | `dumbtrader/agentic/reflection.py` |
| 5. Update (distill) | gated, versioned diff | `dumbtrader/agentic/gate.py` + git commit |

State lives in `dumbtrader/agentic/ledger.py` (SQLite: rollouts, decisions,
lessons).

## Two design forks (decided)

- **Cadence: weekly, firmly.** Daily on a $230 cash account is T+1
  settlement-violation roulette that pays you in noise. One rollout = one week.
- **Autonomy: propose → confirm → execute, every order.** There is no
  auto-firing path in this code. `rollout.execute_confirmed` only acts on
  decisions a `Confirmer` approves, and order placement is an injected
  `Executor` seam. A self-modifying policy with no human gate is the precise
  recipe for waking up to a zeroed account and a great anecdote.

## Invariants

1. **Human gate.** Every order is proposed, surfaced, and confirmed before
   placement.
2. **Frozen objective.** `objective.py` is defined before trading and not edited
   after seeing a result. No moving the goalposts.
3. **Sample-size gate.** No rule change without N pre-registered decisions of
   evidence (`gate.min_decisions`, default 20) or a stat-sig threshold.
4. **Adherence is first-class.** Every override is logged; reflection scores the
   policy *as actually run*.
5. **Regime tagging.** Every lesson is stamped with its market regime.
6. **Kill switch.** At the configured drawdown (−30%), halt and full-review.

## Weekly ritual

1. Pull account state + positions + quotes (Robinhood tools).
2. `dumbtrader-loop propose <snapshot.json>` → review proposed decisions.
3. Confirm each order; execute via broker tools; record fills.
4. `dumbtrader-loop reflect` → fill in decision grades + one candidate lesson.
5. If the gate clears (`dumbtrader-loop gate ...`), edit the playbook, bump the
   version, and commit. Otherwise keep the rule and gather more evidence.
6. Push metrics to the Notion "Agentic Loop" DB.

## Why P&L is not the score

On small samples a great decision loses and a terrible one wins. Optimizing P&L
here just bakes variance into the policy. The headline metrics are **adherence**
(did we run the policy?) and **excess return vs buy-and-hold** (is there any edge
at all?), with hit rate / win-loss / drawdown as diagnostic context for the
reflection.

## Not investment advice

This is a process-design harness that happens to hold real money. Nothing here
is investment advice.
