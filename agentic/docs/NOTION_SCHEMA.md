# Notion "Agentic Loop" DB — Schema

Metrics tracker for the loop. One row per iteration (one week). The playbook and
iteration logs live in git/Obsidian; this DB is the at-a-glance scoreboard.

> The local SQLite ledger (`agentic_loop.db`, schema in
> `dumbtrader/agentic/ledger.py`) is the source of truth. This Notion DB is a
> human-facing mirror — populate it from the ledger each iteration.

## Properties

| Property | Type | Notes |
|----------|------|-------|
| Iteration | Number (title) | Monotonic; one per week |
| Window | Text | e.g. `2026-W24` |
| Policy version | Select | `v0`, `v1`, … (matches playbook frontmatter) |
| Regime | Select | `melt-up`, `chop`, `bear`, `recovery` — tag for every lesson |
| Return % | Number (percent) | Account return over the window |
| Benchmark % | Number (percent) | Buy-and-hold SPY, same window |
| Excess % | Formula | `Return % − Benchmark %` |
| Beat benchmark | Formula (checkbox) | `Excess % > 0` |
| Trades | Number | Closed round-trips this iteration |
| Hit rate | Number (percent) | Wins / trades |
| Avg win / Avg loss | Number / Number | Dollars |
| Win/Loss ratio | Formula | `AvgWin / abs(AvgLoss)` |
| Expectancy | Number | $/trade |
| Max drawdown | Number (percent) | Peak-to-trough on equity curve |
| **Adherence %** | Number (percent) | **Headline metric** — followed / total decisions |
| Overrides | Number | Count of deviations from policy |
| Gate cleared | Checkbox | Did a rule change clear the sample-size gate? |
| Lesson | Text | The one distilled lesson (regime-tagged) |
| Reflection | URL/Relation | Link to `agentic/iterations/iteration-NNN.md` |
| Kill switch | Checkbox | Tripped this iteration? |

## Views

- **Scoreboard** (table): sort by Iteration desc; show Adherence %, Excess %,
  Beat benchmark, Lesson.
- **By regime** (board, grouped by Regime): so a lesson is never read out of the
  regime it was learned in.
- **Policy versions** (board, grouped by Policy version): the changelog of the
  "weights".

## Creating it

Do **not** create this DB unprompted — it writes to the user's Notion workspace.
When the operator asks, it can be created with the Notion tools
(`notion-create-database`) using the properties above, ideally as a Cowork
scheduled task that feeds the page each week.
