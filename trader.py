from __future__ import annotations

import json
from collections import deque
from typing import Dict, List, Tuple

from datamodel import Order, OrderDepth, TradingState

# Safe placeholder: change later when you learn official limits
DEFAULT_POSITION_LIMIT = 20

# ---- knobs (easy to tune) ----
TOM_EMA_ALPHA = 0.15
TOM_FAST_ALPHA = 0.35
TOM_SLOW_ALPHA = 0.06
TOM_WINDOW = 60

# Taking aggressiveness (in vol units)
TOM_TAKE_K_WITH = 0.75
TOM_TAKE_K_AGAINST = 1.25

# Quoting
TOM_BASE_SPREAD_K = 0.55
TOM_TREND_EXTRA_SPREAD = 2
TOM_INV_SKEW_K = 0.20
TOM_TREND_FAIR_K = 0.35

# Only restrict adding inventory when quite loaded
TOM_LOADED_FRAC = 0.75

# Always keep some quoting size
TOM_Q_NORMAL = 8
TOM_Q_TREND = 4

def best_bid_ask(depth: OrderDepth) -> Tuple[int | None, int | None, int | None, int | None]:
    best_bid = None
    best_bid_vol = None
    if getattr(depth, "buy_orders", None):
        best_bid = max(depth.buy_orders.keys())
        best_bid_vol = depth.buy_orders[best_bid]

    best_ask = None
    best_ask_vol = None
    if getattr(depth, "sell_orders", None):
        best_ask = min(depth.sell_orders.keys())
        best_ask_vol = depth.sell_orders[best_ask]  # negative

    return best_bid, best_bid_vol, best_ask, best_ask_vol


class RollingWindow:
    def __init__(self, window: int):
        self.window = int(window)
        self.values = deque(maxlen=self.window)

    def push(self, x: float) -> None:
        self.values.append(float(x))

    def mean(self) -> float | None:
        if not self.values:
            return None
        return sum(self.values) / len(self.values)

    def std(self) -> float:
        n = len(self.values)
        if n < 2:
            return 0.0
        m = self.mean()
        assert m is not None
        var = sum((v - m) ** 2 for v in self.values) / (n - 1)
        return var**0.5


class EMA:
    def __init__(self, alpha: float, value: float | None = None):
        self.alpha = float(alpha)
        self.value = value

    def push(self, x: float) -> float:
        x = float(x)
        if self.value is None:
            self.value = x
        else:
            self.value = self.alpha * x + (1.0 - self.alpha) * self.value
        return float(self.value)


class Trader:
    POSITION_LIMITS = {
        "EMERALDS": DEFAULT_POSITION_LIMIT,
        "TOMATOES": DEFAULT_POSITION_LIMIT,
    }

    def bid(self):
        return 15

    def _load(self, traderData: str) -> dict:
        if not traderData:
            return {}
        try:
            obj = json.loads(traderData)
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}

    def _dump(self, obj: dict) -> str:
        s = json.dumps(obj, separators=(",", ":"))
        return s[:50000]

    def _pos(self, state: TradingState, product: str) -> int:
        return int(getattr(state, "position", {}).get(product, 0))

    def _buy_cap(self, state: TradingState, product: str) -> int:
        lim = int(self.POSITION_LIMITS.get(product, 0))
        return max(0, lim - self._pos(state, product))

    def _sell_cap(self, state: TradingState, product: str) -> int:
        lim = int(self.POSITION_LIMITS.get(product, 0))
        return max(0, lim + self._pos(state, product))

    def run(self, state: TradingState):
        saved = self._load(getattr(state, "traderData", ""))

        # Restore TOMATOES state
        t_win = RollingWindow(window=TOM_WINDOW)
        for v in saved.get("t_win", [])[-TOM_WINDOW:]:
            try:
                t_win.push(float(v))
            except Exception:
                pass

        t_ema = EMA(alpha=TOM_EMA_ALPHA, value=saved.get("t_ema"))
        t_fast = EMA(alpha=TOM_FAST_ALPHA, value=saved.get("t_fast"))
        t_slow = EMA(alpha=TOM_SLOW_ALPHA, value=saved.get("t_slow"))

        result: Dict[str, List[Order]] = {}
        conversions = 0

        for product, depth in getattr(state, "order_depths", {}).items():
            orders: List[Order] = []

            best_bid, best_bid_vol, best_ask, best_ask_vol = best_bid_ask(depth)
            if best_bid is None or best_ask is None:
                result[product] = orders
                continue

            mid = (best_bid + best_ask) / 2.0

            if product == "EMERALDS":
                # Small improvement: quote a bit tighter when flat, wider when loaded
                p = self._pos(state, product)
                skew = max(-8, min(8, p))

                base_bid = 9992
                base_ask = 10008

                bid_px = base_bid - (skew // 2)
                ask_px = base_ask - (skew // 2)

                q = 7 if abs(p) < 8 else 5

                if self._buy_cap(state, product) > 0:
                    orders.append(Order(product, int(bid_px), min(q, self._buy_cap(state, product))))
                if self._sell_cap(state, product) > 0:
                    orders.append(Order(product, int(ask_px), -min(q, self._sell_cap(state, product))))

                # Take obvious edge if it appears
                if best_ask <= base_bid and self._buy_cap(state, product) > 0:
                    qty = min(self._buy_cap(state, product), int(-best_ask_vol))
                    if qty > 0:
                        orders.append(Order(product, int(best_ask), int(qty)))

                if best_bid >= base_ask and self._sell_cap(state, product) > 0:
                    qty = min(self._sell_cap(state, product), int(best_bid_vol))
                    if qty > 0:
                        orders.append(Order(product, int(best_bid), -int(qty)))

            elif product == "TOMATOES":
                # Update signals
                t_win.push(mid)
                fair = t_ema.push(mid)
                fast = t_fast.push(mid)
                slow = t_slow.push(mid)
                trend = fast - slow

                vol = max(1.0, t_win.std())
                p = self._pos(state, product)
                lim = int(self.POSITION_LIMITS.get("TOMATOES", DEFAULT_POSITION_LIMIT))

                # Strong trend threshold scales with vol
                trend_k = max(0.9, 0.30 * vol)
                strong_down = trend < -trend_k
                strong_up = trend > trend_k

                # Smaller trend push into fair (reduce getting run over without killing trades)
                fair_adj = fair + TOM_TREND_FAIR_K * trend

                # Spread/size adapts in strong trend
                base_spread = max(2, int(round(TOM_BASE_SPREAD_K * vol)))
                spread = base_spread + (TOM_TREND_EXTRA_SPREAD if (strong_down or strong_up) else 0)
                q = TOM_Q_NORMAL if not (strong_down or strong_up) else TOM_Q_TREND

                inv_skew = max(-10, min(10, p))
                bid_px = int(round(fair_adj - spread - TOM_INV_SKEW_K * inv_skew))
                ask_px = int(round(fair_adj + spread - TOM_INV_SKEW_K * inv_skew))

                loaded_long = p > TOM_LOADED_FRAC * lim
                loaded_short = p < -TOM_LOADED_FRAC * lim

                allow_buy = not (strong_down and loaded_long)
                allow_sell = not (strong_up and loaded_short)

                # Take (slightly more aggressive with-trend)
                if self._buy_cap(state, product) > 0 and allow_buy:
                    k = TOM_TAKE_K_AGAINST if strong_down else TOM_TAKE_K_WITH
                    if best_ask < fair_adj - k * vol:
                        qty = min(self._buy_cap(state, product), int(-best_ask_vol))
                        if qty > 0:
                            orders.append(Order(product, int(best_ask), int(qty)))

                if self._sell_cap(state, product) > 0 and allow_sell:
                    k = TOM_TAKE_K_AGAINST if strong_up else TOM_TAKE_K_WITH
                    if best_bid > fair_adj + k * vol:
                        qty = min(self._sell_cap(state, product), int(best_bid_vol))
                        if qty > 0:
                            orders.append(Order(product, int(best_bid), -int(qty)))

                # Passive quotes
                if self._buy_cap(state, product) > 0 and allow_buy:
                    orders.append(Order(product, bid_px, min(q, self._buy_cap(state, product))))
                if self._sell_cap(state, product) > 0 and allow_sell:
                    orders.append(Order(product, ask_px, -min(q, self._sell_cap(state, product))))

            result[product] = orders

        traderData = self._dump(
            {
                "t_ema": t_ema.value,
                "t_fast": t_fast.value,
                "t_slow": t_slow.value,
                "t_win": list(t_win.values),
            }
        )

        return result, conversions, traderData
