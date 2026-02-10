"""Core trading engine: connects to ProjectX, runs strategy loop."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from project_x_py import ProjectX, TradingSuite

from dumbtrader.strategy.base import Signal, SignalType
from dumbtrader.trades import TradeRecord, TradeStore

if TYPE_CHECKING:
    from dumbtrader.config import BotConfig
    from dumbtrader.strategy.base import Strategy

logger = logging.getLogger("dumbtrader.engine")

# Map timeframe unit suffixes to ProjectX unit codes.
_UNIT_MAP = {"s": 1, "min": 2, "h": 3, "d": 4, "w": 5, "mo": 6}


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
        self._rest: ProjectX | None = None
        self._running = False
        self._trade_store = TradeStore()
        self._net_position: int = 0  # positive = long, negative = short

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

        await self._trade_store.initialize()

        # Standalone REST client for bar data — independent of the
        # TradingSuite WebSocket which is unreliable (signalrcore bug).
        self._rest = ProjectX(
            username=self.config.api.username,
            api_key=self.config.api.api_key,
        )
        await self._rest.authenticate()
        self._rest.cache_ttl = 0.0  # disable SDK cache; we need fresh bars each tick
        logger.info("REST client authenticated")

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
        if self._rest is not None:
            await self._rest.__aexit__(None, None, None)
            self._rest = None
        await self._trade_store.close()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        assert self._suite is not None and self._rest is not None
        tf = self.config.trading.timeframe
        interval, unit = self._parse_timeframe(tf)
        instrument = self.config.trading.instrument
        logger.info("Entering strategy loop (timeframe=%s)", tf)

        # Sync net position from broker on startup
        pos = await self._get_open_position()
        if pos is not None:
            self._net_position = pos.signed_size
            logger.info("Existing position: %d contracts", self._net_position)
        else:
            self._net_position = 0
            logger.info("No existing position")

        while self._running:
            # Fetch fresh bars from the standalone REST client on every
            # tick.  The TradingSuite WebSocket feed is broken (signalrcore
            # JSON parse error kills it on connect), so we bypass it.
            try:
                df = await self._rest.get_bars(
                    instrument, days=5, interval=interval, unit=unit,
                )
            except Exception:
                logger.exception("Failed to fetch bar data")
                await asyncio.sleep(5)
                continue

            if df is None or df.height == 0:
                logger.debug("No bar data available yet")
                await asyncio.sleep(1)
                continue

            last_row = df.row(-1, named=True)
            logger.info(
                "Tick  bars=%d  last_ts=%s  close=%s",
                df.height,
                last_row.get("timestamp", "?"),
                last_row.get("close", "?"),
            )

            signals = self.strategy.on_bars(df)
            if signals:
                for sig in signals:
                    await self._handle_signal(sig)
            else:
                logger.info("No signals")

            # Wait roughly one bar interval before re-evaluating
            await asyncio.sleep(self._sleep_seconds())

    # ------------------------------------------------------------------
    # Signal handling
    # ------------------------------------------------------------------

    async def _handle_signal(self, signal: Signal) -> None:
        assert self._suite is not None
        ts = datetime.now(timezone.utc).isoformat()
        instrument = self.config.trading.instrument
        contract_id = self._suite.instrument_id or ""
        size = min(signal.size, self.config.trading.max_position_size)

        if self.dry_run:
            side = 0 if signal.type in (SignalType.BUY, SignalType.CLOSE_SHORT) else 1
            logger.info("[DRY-RUN] %s  signal=%s  size=%d  reason=%s", ts, signal.type.name, size, signal.reason)
            pnl = None
            if signal.type in (SignalType.CLOSE_LONG, SignalType.CLOSE_SHORT):
                pnl = 0.0
            await self._trade_store.record_trade(TradeRecord(
                timestamp=ts,
                instrument=instrument,
                contract_id=contract_id,
                signal_type=signal.type.name,
                side=side,
                size=size,
                pnl=pnl,
                strategy=self.strategy.name,
                reason=signal.reason,
                dry_run=1,
            ))
            return

        # --- Position-aware signal handling ---

        if signal.type == SignalType.BUY:
            if self._net_position > 0:
                logger.info("Already long (%d), skipping BUY", self._net_position)
                return
            # If short, flatten first
            if self._net_position < 0:
                close_size = abs(self._net_position)
                logger.info("Flattening short (%d) before going long", self._net_position)
                await self._place_and_record(
                    contract_id=contract_id, instrument=instrument,
                    signal_type="CLOSE_SHORT", side=0, size=close_size,
                    reason="flatten before reversal", is_close=True,
                )
            await self._place_and_record(
                contract_id=contract_id, instrument=instrument,
                signal_type="BUY", side=0, size=size,
                reason=signal.reason, is_close=False,
            )

        elif signal.type == SignalType.SELL:
            if self._net_position < 0:
                logger.info("Already short (%d), skipping SELL", self._net_position)
                return
            # If long, flatten first
            if self._net_position > 0:
                close_size = abs(self._net_position)
                logger.info("Flattening long (%d) before going short", self._net_position)
                await self._place_and_record(
                    contract_id=contract_id, instrument=instrument,
                    signal_type="CLOSE_LONG", side=1, size=close_size,
                    reason="flatten before reversal", is_close=True,
                )
            await self._place_and_record(
                contract_id=contract_id, instrument=instrument,
                signal_type="SELL", side=1, size=size,
                reason=signal.reason, is_close=False,
            )

        elif signal.type == SignalType.CLOSE_LONG:
            if self._net_position <= 0:
                logger.info("Not long (%d), skipping CLOSE_LONG", self._net_position)
                return
            close_size = abs(self._net_position)
            await self._place_and_record(
                contract_id=contract_id, instrument=instrument,
                signal_type="CLOSE_LONG", side=1, size=close_size,
                reason=signal.reason, is_close=True,
            )

        elif signal.type == SignalType.CLOSE_SHORT:
            if self._net_position >= 0:
                logger.info("Not short (%d), skipping CLOSE_SHORT", self._net_position)
                return
            close_size = abs(self._net_position)
            await self._place_and_record(
                contract_id=contract_id, instrument=instrument,
                signal_type="CLOSE_SHORT", side=0, size=close_size,
                reason=signal.reason, is_close=True,
            )

    async def _place_and_record(
        self,
        *,
        contract_id: str,
        instrument: str,
        signal_type: str,
        side: int,
        size: int,
        reason: str,
        is_close: bool,
    ) -> None:
        """Place a market order, fetch fill price, record trade, update net position."""
        assert self._suite is not None
        ts = datetime.now(timezone.utc).isoformat()

        try:
            logger.info("[ORDER] %s %d @ market  reason=%s", signal_type, size, reason)
            resp = await self._suite.orders.place_market_order(
                contract_id=contract_id, side=side, size=size,
            )

            if not resp.success:
                logger.error("Order rejected: %s", resp.errorMessage)
                await self._trade_store.record_trade(TradeRecord(
                    timestamp=ts,
                    instrument=instrument,
                    contract_id=contract_id,
                    signal_type=signal_type,
                    side=side,
                    size=size,
                    order_id=resp.orderId,
                    success=0,
                    error_msg=resp.errorMessage,
                    strategy=self.strategy.name,
                    reason=reason,
                ))
                return

            fill_price, api_pnl = await self._get_fill_price(resp.orderId)

            # Update net position tracking
            if side == 0:  # buy
                self._net_position += size
            else:  # sell
                self._net_position -= size
            logger.info("Net position now: %d", self._net_position)

            # For closing trades, prefer API-computed P&L, fall back to local calc
            pnl = None
            if is_close:
                if api_pnl is not None:
                    pnl = api_pnl
                elif fill_price is not None:
                    close_type = SignalType.CLOSE_LONG if side == 1 else SignalType.CLOSE_SHORT
                    pnl = await self._compute_pnl(close_type, fill_price, instrument, size)

            await self._trade_store.record_trade(TradeRecord(
                timestamp=ts,
                instrument=instrument,
                contract_id=contract_id,
                signal_type=signal_type,
                side=side,
                size=size,
                order_id=resp.orderId,
                fill_price=fill_price,
                pnl=pnl,
                strategy=self.strategy.name,
                reason=reason,
            ))

            if pnl is not None:
                logger.info("Trade P&L: $%.2f", pnl)

        except Exception:
            logger.exception("Order execution failed for %s", signal_type)
            await self._trade_store.record_trade(TradeRecord(
                timestamp=ts,
                instrument=instrument,
                contract_id=contract_id,
                signal_type=signal_type,
                side=side,
                size=size,
                success=0,
                error_msg="exception during order execution",
                strategy=self.strategy.name,
                reason=reason,
            ))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_fill_price(self, order_id: int) -> tuple[float | None, float | None]:
        """Get fill price and P&L from the trades endpoint.

        The SDK's ``search_trades()`` hits ``GET /trades/search`` which 404s
        on TopstepX.  The actual working endpoint is ``POST /Trade/search``.
        """
        assert self._rest is not None
        for attempt in range(4):
            await asyncio.sleep(0.3 * (attempt + 1))
            try:
                resp = await self._rest._make_request(
                    "POST",
                    "/Trade/search",
                    data={"accountId": self._rest.account_info.id},
                )
                for trade in resp.get("trades", []):
                    if trade.get("orderId") == order_id:
                        return trade.get("price"), trade.get("profitAndLoss")
            except Exception:
                logger.debug("Failed to search trades for order %d", order_id)
        logger.warning("Could not get fill for order %d", order_id)
        return None, None

    async def _get_open_position(self) -> object | None:
        """Get the current open position for our contract."""
        assert self._rest is not None
        try:
            positions = await self._rest.search_open_positions()
            contract_id = self._suite.instrument_id
            for pos in positions:
                if pos.contractId == contract_id:
                    return pos
        except Exception:
            logger.exception("Failed to search open positions")
        return None

    async def _compute_pnl(
        self, signal_type: SignalType, exit_price: float, instrument: str, size: int,
    ) -> float | None:
        """Compute P&L for a closing trade by looking up the entry from the DB."""
        assert self._suite is not None
        inst = self._suite.instrument
        if inst is None:
            return None

        entry = await self._trade_store.get_last_entry(instrument)
        if entry is None:
            logger.warning("No entry trade found for P&L calculation")
            return None

        entry_price, _entry_side = entry
        tick_value_per_point = inst.tickValue / inst.tickSize

        if signal_type == SignalType.CLOSE_LONG:
            return (exit_price - entry_price) * size * tick_value_per_point
        else:  # CLOSE_SHORT
            return (entry_price - exit_price) * size * tick_value_per_point

    @staticmethod
    def _parse_timeframe(tf: str) -> tuple[int, int]:
        """Parse a timeframe string like '1min' into (interval, unit_code)."""
        tf = tf.lower()
        for suffix, code in _UNIT_MAP.items():
            if tf.endswith(suffix):
                return int(tf[: -len(suffix)]), code
        return 1, 2  # fallback: 1 minute

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
