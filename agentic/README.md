# Agentic Loop

A human-gated, weekly self-improvement loop whose product is a versioned
**playbook**, not P&L. The dollars are test fixtures; the score is loop
integrity. Full rationale in [`docs/DESIGN.md`](docs/DESIGN.md).

```
act  ->  score  ->  reflect  ->  distill a lesson into the playbook  ->  repeat
```

## Layout

```
agentic/
  playbook/v0.md          # the policy ("weights") — git-diffable, Obsidian-ready
  iterations/             # per-week reflections (TEMPLATE.md + generated logs)
  docs/DESIGN.md          # the design + invariants
  docs/NOTION_SCHEMA.md   # the Notion "Agentic Loop" tracker schema
  snapshot.example.json   # sample input for `dumbtrader-loop propose`
dumbtrader/agentic/       # the harness (playbook, objective, gate, guardrails,
                          # ledger, reflection, rollout, cli)
```

## CLI

Installed as `dumbtrader-loop` (see `pyproject.toml`).

```bash
# show the active policy + loop state
dumbtrader-loop status

# apply the policy to a market snapshot and preview proposed decisions
dumbtrader-loop propose agentic/snapshot.example.json

# record decisions through the human gate (interactive confirm; dry-run fills)
dumbtrader-loop propose agentic/snapshot.example.json --execute

# render the weekly post-mortem template for the latest iteration
dumbtrader-loop reflect --equity-start 230 --equity-end 234 --benchmark-return 0.012

# check whether a pre-registered rule change may be applied
dumbtrader-loop gate --hypothesis "tighter 8% stop raises expectancy" \
  --rule exit.stop_pct --regime chop --observed 12
```

## Invariants (do not remove)

1. **Human gate** — every order proposed → confirmed → executed. No auto-firing.
2. **Frozen objective** — defined before trading, never edited after a result.
3. **Sample-size gate** — no rule change without ≥ N decisions (or stat-sig).
4. **Adherence logged** — every override recorded; reflection scores the policy
   as actually run.
5. **Regime tagging** — every lesson stamped with its market regime.
6. **Kill switch** — halt for full review at the configured drawdown.

Not investment advice — a process-design harness that happens to hold real money.
