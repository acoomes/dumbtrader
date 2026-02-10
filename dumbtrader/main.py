"""CLI entry point for dumbtrader."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

from dumbtrader.config import load_config


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dumbtrader",
        description="Futures trading bot powered by ProjectX",
    )
    p.add_argument(
        "-c", "--config",
        default="config.yaml",
        help="Path to the global config file (default: config.yaml)",
    )
    p.add_argument(
        "--live",
        action="store_true",
        default=False,
        help="Enable live trading (requires confirmation)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Run the strategy loop but don't place real orders",
    )
    p.add_argument(
        "--yes", "-y",
        action="store_true",
        default=False,
        help="Skip interactive live-trading confirmation (use when launched by the dashboard)",
    )
    p.add_argument(
        "--dashboard",
        action="store_true",
        default=False,
        help="Launch the web dashboard instead of running the bot directly",
    )
    p.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port for the web dashboard (default: 8080)",
    )
    return p


def _load_strategy(strategy_path: Path):
    """Instantiate the right strategy class based on file extension."""
    from dumbtrader.strategy.pine_strategy import PineStrategy
    from dumbtrader.strategy.yaml_strategy import YamlStrategy

    ext = strategy_path.suffix.lower()
    if ext in (".yaml", ".yml"):
        return YamlStrategy(strategy_path)
    if ext == ".pine":
        return PineStrategy(strategy_path)
    raise ValueError(f"Unsupported strategy file type: {ext!r}")


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    # Force UTF-8 on stdout/stderr so emoji from project-x-py SDK doesn't crash on Windows
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")

    # Dashboard mode — launch web UI instead of running the bot directly
    if args.dashboard:
        import uvicorn
        from dumbtrader.dashboard import app as dashboard_app

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )
        logger = logging.getLogger("dumbtrader")
        logger.info("Starting dashboard on http://localhost:%d", args.port)
        uvicorn.run(dashboard_app, host="0.0.0.0", port=args.port, log_level="info")
        return

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger("dumbtrader")

    # Load config
    config = load_config(args.config)

    # Safety: live mode requires --live flag + confirmation
    if config.trading.mode == "live":
        if not args.live:
            logger.error("Config is set to live trading but --live flag was not passed. Aborting.")
            sys.exit(1)
        if not args.yes:
            print("WARNING: You are about to trade with REAL money.")
            answer = input("Type 'yes' to confirm: ")
            if answer.strip().lower() != "yes":
                logger.info("Live trading not confirmed. Exiting.")
                sys.exit(0)

    # Load strategy
    strategy_path = config.strategy_path()
    if not strategy_path.exists():
        logger.error("Strategy file not found: %s", strategy_path)
        sys.exit(1)

    strategy = _load_strategy(strategy_path)
    logger.info("Loaded strategy: %s  (%s)", strategy.name, strategy_path)

    # Build engine
    from dumbtrader.engine import Engine
    engine = Engine(config, strategy, dry_run=args.dry_run)

    # Graceful shutdown on SIGINT / SIGTERM
    loop = asyncio.new_event_loop()

    def _shutdown(sig: int, frame) -> None:
        logger.info("Received signal %s, shutting down…", sig)
        loop.call_soon_threadsafe(loop.stop)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        loop.run_until_complete(engine.start())
    except KeyboardInterrupt:
        logger.info("Interrupted")
    finally:
        loop.run_until_complete(engine.stop())
        loop.close()


if __name__ == "__main__":
    main()
