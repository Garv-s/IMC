from collections import deque
import math


class RollingStats:
    def __init__(self, window: int):
        self.window = int(window)
        self.values = deque(maxlen=self.window)

    def push(self, x: float) -> None:
        self.values.append(float(x))

    def mean(self):
        if not self.values:
            return None
        return sum(self.values) / len(self.values)

    def std(self):
        n = len(self.values)
        if n < 2:
            return 0.0
        m = self.mean()
        var = sum((v - m) ** 2 for v in self.values) / (n - 1)
        return math.sqrt(var)


class Ema:
    def __init__(self, alpha: float):
        self.alpha = float(alpha)
        self.value = None

    def push(self, x: float) -> float:
        x = float(x)
        if self.value is None:
            self.value = x
        else:
            self.value = self.alpha * x + (1.0 - self.alpha) * self.value
        return self.value


class Trader:
    """
    Single-file submission entrypoint.

    The platform's app.py does:
        orders, conversion, traderData = trader.run(trading_state)

    So run() MUST return exactly (orders, conversion, traderData).
    """

    def __init__(self):
        self.emeralds_fair = 10000.0
        self.tomatoes_ema = Ema(alpha=0.15)
        self.tomatoes_stats = RollingStats(window=50)

    def run(self, trading_state):
        # --- Minimal safe outputs matching required contract ---
        # orders: dict[str, list[Order]] (we don't know Order type yet)
        orders = {}

        # conversion: int (0 if unused)
        conversion = 0

        # traderData: str (persisted state; keep empty for now)
        traderData = ""

        return orders, conversion, traderData
