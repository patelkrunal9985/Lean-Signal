import json
import os


FAKE_TEST_TICKERS = frozenset({"RSN", "DFLT", "COOL", "FRCE"})


class LossTracker:
    LOSS_TRACK_PATH = "models/loss_tracker.json"

    def __init__(self, path: str = None):
        self.path = path or self.LOSS_TRACK_PATH
        self.consecutive_losses: dict[str, int] = {}
        self.portfolio_consecutive_losses: int = 0
        self.daily_pnl: float = 0.0
        self.daily_trades: int = 0
        self.current_drawdown: float = 0.0
        self.load()

    def record_trade(self, ticker: str, pnl: float, strategy: str = ""):
        if pnl > 0:
            self.consecutive_losses[ticker] = 0
            self.portfolio_consecutive_losses = 0
        else:
            self.consecutive_losses[ticker] = self.consecutive_losses.get(ticker, 0) + 1
            self.portfolio_consecutive_losses += 1
        self.daily_pnl += pnl
        self.daily_trades += 1

    def get_consecutive_losses(self, ticker: str) -> int:
        return self.consecutive_losses.get(ticker, 0)

    def get_portfolio_consecutive_losses(self) -> int:
        return self.portfolio_consecutive_losses

    def reset_daily(self):
        self.daily_pnl = 0.0
        self.daily_trades = 0

    def get_daily_loss_pct(self, account_value: float) -> float:
        if account_value <= 0:
            return 0.0
        return abs(min(self.daily_pnl, 0)) / account_value

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        # NOTE: daily_pnl and daily_trades are NOT persisted — they reset daily
        # via reset_daily(). Persisting them would cause stale values to bleed
        # into the next session and break test isolation.
        data = {
            "consecutive_losses": self.consecutive_losses,
            "portfolio_consecutive_losses": self.portfolio_consecutive_losses,
            "current_drawdown": self.current_drawdown,
        }
        with open(self.path, "w") as f:
            json.dump(data, f, indent=2)

    def load(self):
        if not os.path.exists(self.path):
            return
        with open(self.path) as f:
            data = json.load(f)
        # Only restore persisted fields (daily_trades/daily_pnl reset daily)
        self.consecutive_losses = data.get("consecutive_losses", {})
        self.portfolio_consecutive_losses = data.get("portfolio_consecutive_losses", 0)
        self.current_drawdown = data.get("current_drawdown", 0.0)
        # Never restore daily_trades or daily_pnl from disk — they reset daily

    def remove_ticker(self, ticker: str):
        self.consecutive_losses.pop(ticker, None)

    def purge_fake_tickers(self):
        for t in FAKE_TEST_TICKERS:
            self.remove_ticker(ticker=t)
        self.save()

    @classmethod
    def purge_fake_tickers_from_disk(cls, path: str = None):
        path = path or cls.LOSS_TRACK_PATH
        if not os.path.exists(path):
            return
        with open(path) as f:
            data = json.load(f)
        cons = data.get("consecutive_losses", {})
        changed = any(t in cons for t in FAKE_TEST_TICKERS)
        if changed:
            for t in FAKE_TEST_TICKERS:
                cons.pop(t, None)
            with open(path, "w") as f:
                json.dump(data, f, indent=2)

    def reset(self, clear_disk: bool = False):
        """Full reset of all state (for testing and disaster recovery).

        Args:
            clear_disk: If True, also deletes the persisted JSON file so stale
                        state doesn't reappear after a server restart.
        """
        self.consecutive_losses.clear()
        self.portfolio_consecutive_losses = 0
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.current_drawdown = 0.0
        if clear_disk:
            try:
                os.unlink(self.path)
            except FileNotFoundError:
                pass
