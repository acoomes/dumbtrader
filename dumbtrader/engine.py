"""Core trading engine: connects to ProjectX, runs strategy loop."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING

from project_x_py import TradingSuite

from dumbtrader.strategy.base import Signal, SignalType

if TYPE_CHECKING:
    from dumbtrader.config import BotConfig
    from dumbtrader.strategy.base import Strategy

logger = logging.getLogger("dumbtrader.engine")


class Engine:
    """Connects to ProjectX and continuously evaluates a strategy."""

    def __init__(
        self,
        config: BotConfig,
        strategy: Strategy,
        *,
        dry_run: bool = False,
    ) -> None:
        self.config = config
        self.strategy = strategy
        self.dry_run = dry_run
        self._suite: TradingSuite | None = None
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Connect to ProjectX and start the strategy loop."""
        logger.info(
            "Starting engine  instrument=%s  timeframe=%s  mode=%s  dry_run=%s",
            self.config.trading.instrument,
            self.config.trading.timeframe,
            self.config.trading.mode,
            self.dry_run,
        )

        self._suite = await TradingSuite.create(
            self.config.trading.instrument,
            timeframes=[self.config.trading.timeframe],
        )
        logger.info("Connected to ProjectX")
        self._running = True

        try:
            await self._loop()
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Disconnect gracefully."""
        self._running = False
        if self._suite is not None:
            logger.info("Disconnecting from ProjectX")
            await self._suite.disconnect()
            self._suite = None

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        assert self._suite is not None
        tf = self.config.trading.timeframe
        logger.info("Entering strategy loop (timeframe=%s)", tf)

        while self._running:
            try:
                df = await self._suite.data.get_data(tf)
            except Exception:
                logger.exception("Failed to fetch bar data")
                await asyncio.sleep(5)
                continue

            if df is None or df.height == 0:
                await asyncio.sleep(1)
                continue

            signals = self.strategy.on_bars(df)
            for sig in signals:
                await self._handle_signal(sig)

            # Wait roughly one bar interval before re-evaluating
            await asyncio.sleep(self._sleep_seconds())

    # ------------------------------------------------------------------
    # Signal handling
    # ------------------------------------------------------------------

    async def _handle_signal(self, signal: Signal) -> None:
        assert self._suite is not None
        ts = datetime.utcnow().isoformat()

        # Enforce max position size
        size = min(signal.size, self.config.trading.max_position_size)

        if self.dry_run:
            logger.info("[DRY-RUN] %s  signal=%s  size=%d  reason=%s", ts, signal.type.name, size, signal.reason)
            return

        contract_id = self._suite.instrument_id
        try:
            if signal.type == SignalType.BUY:
                logger.info("[ORDER] BUY %d @ market  reason=%s", size, signal.reason)
                await self._suite.orders.place_market_order(
                    contract_id=contract_id, side=0, size=size,
                )

            elif signal.type == SignalType.SELL:
                logger.info("[ORDER] SELL %d @ market  reason=%s", size, signal.reason)
                await self._suite.orders.place_market_order(
                    contract_id=contract_id, side=1, size=size,
                )

            elif signal.type in (SignalType.CLOSE_LONG, SignalType.CLOSE_SHORT):
                side = 1 if signal.type == SignalType.CLOSE_LONG else 0
                logger.info("[ORDER] %s @ market  reason=%s", signal.type.name, signal.reason)
                pos = await self._suite.positions.get_position(self.config.trading.instrument)
                if pos is not None:
                    await self._suite.orders.place_market_order(
                        contract_id=contract_id, side=side, size=size,
                    )
                else:
                    logger.info("No open position to close")
        except Exception:
            logger.exception("Order execution failed for signal %s", signal)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _sleep_seconds(self) -> float:
        """Return a sleep duration derived from the configured timeframe."""
        tf = self.config.trading.timeframe.lower()
        if "min" in tf:
            try:
                minutes = int(tf.replace("min", ""))
                return float(minutes * 60)
            except ValueError:
                pass
        return 60.0
