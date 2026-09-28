"""Hyperliquid venue adapter: post-only quoting through the official SDK. TESTNET ONLY.

Per block the loop calls on_block (book from the live feed) and sync(); sync
pulls position and account value every block (clearinghouseState, weight 2)
and reconciles open orders and new fills every `reconcile_s` (weight 20 each),
keeping REST use under Hyperliquid's 1200 weight/minute. Orders we place are
tracked locally between reconciles.

A dead-man's switch (scheduleCancel) is re-armed every block: if the process
stalls, the exchange cancels every open order `dead_man_s` later by itself.

ponytail: fills arrive up to `reconcile_s` late; subscribe to the userFills
websocket if that lag starts to matter.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from jev_trader.domain import Fill, Order, PnL
from jev_trader.execution.base import OrderRejected, VenueHealth
from jev_trader.state.book import OrderBook
from jev_trader.state.events import BlockEvent

logger = logging.getLogger(__name__)

TESTNET_API_URL = "https://api.hyperliquid-testnet.xyz"
MIN_ORDER_USD = 10.0
POST_ONLY = {"limit": {"tif": "Alo"}}


def round_price(price: float, sz_decimals: int) -> float:
    """Hyperliquid perp prices: <= 5 significant figures and <= 6 - szDecimals decimals."""
    if price <= 0:
        return price
    return round(float(f"{price:.5g}"), 6 - sz_decimals)


@dataclass(slots=True)
class _Open:
    order: Order
    placed_at: float


class HyperliquidVenue:
    def __init__(
        self,
        *,
        coin: str,
        exchange: Any,
        info: Any,
        address: str,
        sz_decimals: int,
        reconcile_s: float = 5.0,
        dead_man_s: float = 10.0,
    ) -> None:
        self._coin = coin
        self._exchange = exchange
        self._info = info
        self._address = address
        self._sz_decimals = sz_decimals
        self._reconcile_s = reconcile_s
        self._dead_man_s = dead_man_s
        # one SDK call at a time: nonces are millisecond timestamps and must not repeat
        self._lock = asyncio.Lock()
        self._book: OrderBook | None = None
        self._orders: dict[str, _Open] = {}
        self._fill_queue: list[Fill] = []
        self._seen_fills: set[Any] = set()
        self._last_reconcile = 0.0
        self._fills_since_ms = int(time.time() * 1000)
        self._position = 0.0
        self._entry = 0.0
        self._unrealized = 0.0
        self._account_value = 0.0
        self._starting_value: float | None = None
        self._realized = 0.0
        self._fees = 0.0
        self._position_opened: float | None = None
        self._attempts = 0
        self._rejects = 0
        self._fills = 0
        self._dead_man_ok = True

    @classmethod
    def connect(
        cls, *, coin: str, private_key: str, account_address: str, **kwargs: Any
    ) -> HyperliquidVenue:
        """Build the SDK clients against TESTNET. There is deliberately no mainnet option."""
        import eth_account
        from hyperliquid.exchange import Exchange
        from hyperliquid.info import Info

        wallet = eth_account.Account.from_key(private_key)
        info = Info(TESTNET_API_URL, skip_ws=True)
        exchange = Exchange(wallet, TESTNET_API_URL, account_address=account_address)
        assets = {a["name"]: a for a in info.meta()["universe"]}
        if coin not in assets:
            raise ValueError(f"{coin} is not listed on Hyperliquid testnet")
        return cls(
            coin=coin,
            exchange=exchange,
            info=info,
            address=account_address,
            sz_decimals=assets[coin]["szDecimals"],
            **kwargs,
        )

    async def _call(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        async with self._lock:
            return await asyncio.to_thread(fn, *args, **kwargs)

    # --- market data and state -------------------------------------------------

    def on_block(self, event: BlockEvent) -> None:
        self._book = event.book

    async def sync(self) -> None:
        await self._refresh_position()
        now = time.time()
        if now - self._last_reconcile >= self._reconcile_s:
            self._last_reconcile = now
            await self._reconcile_orders()
            await self._pull_fills()
        await self._arm_dead_man(now)

    async def _refresh_position(self) -> None:
        state = await self._call(self._info.user_state, self._address)
        self._account_value = float(state["marginSummary"]["accountValue"])
        if self._starting_value is None:
            self._starting_value = self._account_value
        position = next(
            (p["position"] for p in state["assetPositions"] if p["position"]["coin"] == self._coin),
            None,
        )
        size = float(position["szi"]) if position else 0.0
        if size != 0 and self._position == 0:
            self._position_opened = time.time()
        elif size == 0:
            self._position_opened = None
        self._position = size
        self._entry = float(position["entryPx"] or 0.0) if position else 0.0
        self._unrealized = float(position["unrealizedPnl"]) if position else 0.0

    async def _reconcile_orders(self) -> None:
        """The exchange is the source of truth: drop orders it no longer has, and
        cancel orders we did not place (orphans of a crashed run; the account is
        this bot's alone). Re-arming the dead-man's switch would keep them alive."""
        live = await self._call(self._info.open_orders, self._address)
        live_ids = {str(o["oid"]) for o in live if o["coin"] == self._coin}
        for oid in set(self._orders) - live_ids:
            del self._orders[oid]
        orphans = sorted(live_ids - set(self._orders))
        if orphans:
            logger.warning("cancelling orders not placed by this process: %s", orphans)
            requests = [{"coin": self._coin, "oid": int(oid)} for oid in orphans]
            result = await self._call(self._exchange.bulk_cancel, requests)
            if result.get("status") != "ok":
                logger.error("orphan cancel failed: %s", result)

    async def _pull_fills(self) -> None:
        fills = await self._call(self._info.user_fills_by_time, self._address, self._fills_since_ms)
        for f in fills:
            key = f.get("tid") or (f.get("hash"), f["oid"], f["time"], f["sz"])
            if f["coin"] != self._coin or key in self._seen_fills:
                continue
            self._seen_fills.add(key)
            self._fills_since_ms = max(self._fills_since_ms, int(f["time"]))
            fee = float(f.get("fee", 0.0))
            self._fees += fee
            self._realized += float(f.get("closedPnl", 0.0))
            self._fills += 1
            side = "buy" if f["side"] == "B" else "sell"
            self._fill_queue.append(Fill(side, float(f["px"]), float(f["sz"]), fee))

    async def _arm_dead_man(self, now: float) -> None:
        if not self._dead_man_ok:
            return
        try:
            result = await self._call(
                self._exchange.schedule_cancel, int((now + self._dead_man_s) * 1000)
            )
        except Exception:
            logger.exception("scheduleCancel failed; dead-man's switch disabled")
            self._dead_man_ok = False
            return
        if result.get("status") != "ok":
            logger.error("scheduleCancel rejected (%s); dead-man's switch disabled", result)
            self._dead_man_ok = False

    def drain_fills(self) -> tuple[Fill, ...]:
        fills = tuple(self._fill_queue)
        self._fill_queue.clear()
        return fills

    def book(self) -> OrderBook:
        if self._book is None:
            raise RuntimeError("no book yet")
        return self._book

    def inventory(self) -> float:
        return self._position

    def equity(self) -> float:
        return self._account_value

    def pnl(self) -> PnL:
        return PnL(realized=self._realized, unrealized=self._unrealized, fees=self._fees)

    def health(self) -> VenueHealth:
        if self._attempts == 0:
            return VenueHealth()
        return VenueHealth(
            fill_ratio=self._fills / self._attempts,
            reject_rate=self._rejects / self._attempts,
        )

    def position_age_s(self, now: float) -> float:
        if self._position_opened is None:
            return 0.0
        return max(0.0, time.time() - self._position_opened)

    def open_orders(self) -> dict[str, Order]:
        return {oid: o.order for oid, o in self._orders.items()}

    # --- actions ---------------------------------------------------------------

    async def place(self, order: Order) -> str:
        self._attempts += 1
        size = round(order.size, self._sz_decimals)
        price = round_price(order.price, self._sz_decimals)
        if size <= 0 or size * price < MIN_ORDER_USD:
            self._rejects += 1
            raise OrderRejected(f"order value {size * price:.2f} below ${MIN_ORDER_USD:.0f}")
        result = await self._call(
            self._exchange.order,
            self._coin,
            order.side == "buy",
            size,
            price,
            POST_ONLY,
        )
        status = _first_status(result)
        if "resting" in status:
            oid = str(status["resting"]["oid"])
            self._orders[oid] = _Open(Order(order.side, price, size), time.time())
            return oid
        self._rejects += 1
        if "filled" in status:  # post-only should never fill on entry; flag it loudly
            logger.error("post-only order filled immediately: %s", status)
            return str(status["filled"]["oid"])
        raise OrderRejected(status.get("error", str(result)))

    async def cancel(self, oid: str) -> bool:
        if oid not in self._orders:
            return False
        result = await self._call(self._exchange.cancel, self._coin, int(oid))
        del self._orders[oid]  # gone either way: cancelled now, or already filled/cancelled
        return _first_status(result) == "success"

    async def cancel_all(self) -> int:
        if not self._orders:
            return 0
        requests = [{"coin": self._coin, "oid": int(oid)} for oid in self._orders]
        result = await self._call(self._exchange.bulk_cancel, requests)
        count = len(self._orders)
        self._orders.clear()
        if result.get("status") != "ok":
            logger.error("bulk cancel failed: %s", result)
        return count

    async def flatten(self) -> Fill | None:
        """Kill switch: cancel every open order the EXCHANGE lists (including ones from
        a previous, crashed run), then close the position with a market order."""
        live = await self._call(self._info.open_orders, self._address)
        requests = [{"coin": self._coin, "oid": o["oid"]} for o in live if o["coin"] == self._coin]
        if requests:
            result = await self._call(self._exchange.bulk_cancel, requests)
            if result.get("status") != "ok":
                logger.critical("kill switch cancel failed: %s", result)
        self._orders.clear()
        await self._refresh_position()
        if self._position == 0:
            return None
        result = await self._call(self._exchange.market_close, self._coin)
        status = _first_status(result)
        if "filled" not in status:
            logger.critical("flatten did not fill: %s", result)
            return None
        filled = status["filled"]
        side = "sell" if self._position > 0 else "buy"
        return Fill(side, float(filled["avgPx"]), float(filled["totalSz"]), 0.0)


def _first_status(result: Any) -> Any:
    """First per-order status of an exchange response, or {"error": ...} if the call failed."""
    if not isinstance(result, dict) or result.get("status") != "ok":
        return {"error": f"request failed: {result}"}
    statuses = result["response"]["data"]["statuses"]
    return statuses[0] if statuses else {"error": "empty response"}
