"""Load and validate global config and strategy files."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


def _interpolate_env(value: str) -> str:
    """Replace ${VAR} placeholders with environment variable values."""

    def _replace(match: re.Match) -> str:
        var = match.group(1)
        val = os.environ.get(var)
        if val is None:
            raise ValueError(f"Environment variable {var!r} is not set")
        return val

    return re.sub(r"\$\{(\w+)\}", _replace, value)


def _interpolate_recursive(obj: Any) -> Any:
    if isinstance(obj, str):
        return _interpolate_env(obj)
    if isinstance(obj, dict):
        return {k: _interpolate_recursive(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_interpolate_recursive(v) for v in obj]
    return obj


@dataclass
class ApiConfig:
    username: str
    api_key: str


@dataclass
class TradingConfig:
    mode: str  # "paper" or "live"
    instrument: str
    timeframe: str
    max_position_size: int = 2


@dataclass
class StrategyFileConfig:
    file: str


@dataclass
class BotConfig:
    api: ApiConfig
    trading: TradingConfig
    strategy: StrategyFileConfig
    _base_dir: Path = field(default_factory=lambda: Path("."))

    def strategy_path(self) -> Path:
        return self._base_dir / self.strategy.file


def load_config(config_path: str | Path) -> BotConfig:
    """Load the global bot config from a YAML file.

    Environment variables are loaded from a .env file in the same directory
    as the config file (if present), then ${VAR} placeholders are resolved.
    """
    config_path = Path(config_path)
    base_dir = config_path.parent

    # Load .env from the project root (same dir as config)
    env_file = base_dir / ".env"
    if env_file.exists():
        load_dotenv(env_file)

    with open(config_path) as f:
        raw = yaml.safe_load(f)

    data = _interpolate_recursive(raw)

    api = ApiConfig(
        username=data["api"]["username"],
        api_key=data["api"]["api_key"],
    )
    trading = TradingConfig(
        mode=data["trading"]["mode"],
        instrument=data["trading"]["instrument"],
        timeframe=data["trading"]["timeframe"],
        max_position_size=data["trading"].get("max_position_size", 2),
    )
    strategy = StrategyFileConfig(file=data["strategy"]["file"])

    if trading.mode not in ("paper", "live"):
        raise ValueError(f"Invalid trading mode: {trading.mode!r} (must be 'paper' or 'live')")

    return BotConfig(api=api, trading=trading, strategy=strategy, _base_dir=base_dir)


def load_strategy_file(path: Path) -> dict:
    """Load a YAML strategy definition and return the raw dict."""
    with open(path) as f:
        return yaml.safe_load(f)
