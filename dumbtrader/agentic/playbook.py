"""The versioned trading playbook — the "weights" of the loop.

A playbook is a plain-text markdown file with a YAML frontmatter block.  The
frontmatter holds the *machine-readable* parameters the harness needs (sizing
caps, gate thresholds, benchmark, universe); the markdown body holds the
*human-readable* rules, rationale, and regime notes that a person reads and a
panel reflects on.

Keeping both in one git-tracked file means a policy change is a reviewable
diff — the same muscle as a dated branch — and the file is portable straight
into an Obsidian vault.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("dumbtrader.agentic.playbook")


@dataclass
class SizingRule:
    """How much capital a single position may risk."""

    risk_fraction: float  # max fraction of equity risked per trade (stop loss)
    assumed_stop_pct: float  # assumed adverse move to the stop, as a fraction
    max_positions: int  # max concurrent open positions

    def __post_init__(self) -> None:
        if not 0 < self.risk_fraction <= 0.10:
            # Survival cap: the plan fixes per-trade risk at 5-10% of account.
            raise ValueError(
                f"risk_fraction must be in (0, 0.10]; got {self.risk_fraction}"
            )
        if not 0 < self.assumed_stop_pct < 1:
            raise ValueError(
                f"assumed_stop_pct must be in (0, 1); got {self.assumed_stop_pct}"
            )
        if self.max_positions < 1:
            raise ValueError("max_positions must be >= 1")


@dataclass
class Guardrails:
    """Survival limits that sit above any individual rule."""

    max_loss_per_trade_fraction: float  # hard cap on realized loss per trade
    kill_switch_drawdown: float  # account drawdown that halts trading for review

    def __post_init__(self) -> None:
        if not 0 < self.max_loss_per_trade_fraction <= 0.10:
            raise ValueError("max_loss_per_trade_fraction must be in (0, 0.10]")
        if not 0 < self.kill_switch_drawdown < 1:
            raise ValueError("kill_switch_drawdown must be in (0, 1)")


@dataclass
class GateConfig:
    """The sample-size gate on policy changes."""

    min_decisions: int  # decisions of evidence required before a rule changes

    def __post_init__(self) -> None:
        if self.min_decisions < 1:
            raise ValueError("min_decisions must be >= 1")


@dataclass
class Playbook:
    """A loaded, versioned policy."""

    version: str
    cadence: str  # e.g. "weekly"
    benchmark: str  # e.g. "SPY"
    universe: list[str]
    sizing: SizingRule
    guardrails: Guardrails
    gate: GateConfig
    objective: str  # one-line statement of the frozen reward
    entry: dict[str, Any] = field(default_factory=dict)
    exit: dict[str, Any] = field(default_factory=dict)
    body: str = ""  # the markdown rationale below the frontmatter
    source_path: Path | None = None

    def summary(self) -> str:
        return (
            f"playbook {self.version}  cadence={self.cadence}  "
            f"benchmark={self.benchmark}  universe={','.join(self.universe)}  "
            f"max_positions={self.sizing.max_positions}  "
            f"risk/trade={self.sizing.risk_fraction:.0%}  "
            f"kill@-{self.guardrails.kill_switch_drawdown:.0%}  "
            f"gate>={self.gate.min_decisions}"
        )


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Split a markdown file into (yaml_frontmatter, body).

    Frontmatter is delimited by a leading ``---`` line and a closing ``---``.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(
            "playbook must begin with a '---' YAML frontmatter block"
        )
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            front = "\n".join(lines[1:i])
            body = "\n".join(lines[i + 1 :]).strip()
            return front, body
    raise ValueError("unterminated frontmatter block (missing closing '---')")


def load_playbook(path: str | Path) -> Playbook:
    """Load and validate a playbook markdown file."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    front, body = _split_frontmatter(text)
    data = yaml.safe_load(front) or {}

    try:
        pb = Playbook(
            version=str(data["version"]),
            cadence=str(data["cadence"]),
            benchmark=str(data["benchmark"]),
            universe=list(data["universe"]),
            sizing=SizingRule(**data["sizing"]),
            guardrails=Guardrails(**data["guardrails"]),
            gate=GateConfig(**data["gate"]),
            objective=str(data.get("objective", "")),
            entry=dict(data.get("entry", {})),
            exit=dict(data.get("exit", {})),
            body=body,
            source_path=path,
        )
    except KeyError as exc:  # pragma: no cover - config error path
        raise ValueError(f"playbook {path} missing required key: {exc}") from exc

    logger.info("Loaded %s", pb.summary())
    return pb
