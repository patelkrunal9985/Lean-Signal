import json
import os
from dataclasses import dataclass, field


@dataclass
class StrategySignature:
    regime: str
    instrument_type: str
    active_strategies: tuple[str, ...]
    directions: tuple[str, ...]
    confidences: tuple[float, ...]
    timeframe_hash: str
    direction: str


class StatisticalPosterior:
    POSTERIOR_PATH = "models/posterior_stats.json"

    def __init__(self):
        self.strategy_stats: dict[str, list[int]] = {}
        self.pair_stats: dict[str, list[int]] = {}
        self.regime_stats: dict[str, dict[str, list[int]]] = {}
        self.global_stats = [1, 4]  # Weak prior: 20% win rate (prevents startup overconfidence)
        self.total_signals = 0
        self.load()

    def update(self, signature: StrategySignature, pnl: float):
        is_win = pnl > 0
        for i, strat in enumerate(signature.active_strategies):
            key = f"{strat}:{signature.directions[i]}"
            if key not in self.strategy_stats:
                self.strategy_stats[key] = [1, 4]
            if is_win:
                self.strategy_stats[key][0] += 1
            else:
                self.strategy_stats[key][1] += 1
        sorted_pairs = sorted(zip(signature.active_strategies, signature.directions))
        pair_key = str(frozenset(sorted_pairs))
        if pair_key not in self.pair_stats:
            self.pair_stats[pair_key] = [1, 4]
        if is_win:
            self.pair_stats[pair_key][0] += 1
        else:
            self.pair_stats[pair_key][1] += 1
        regime_key = signature.regime
        instr_key = signature.instrument_type
        if regime_key not in self.regime_stats:
            self.regime_stats[regime_key] = {}
        if instr_key not in self.regime_stats[regime_key]:
            self.regime_stats[regime_key][instr_key] = [1, 4]
        if is_win:
            self.regime_stats[regime_key][instr_key][0] += 1
        else:
            self.regime_stats[regime_key][instr_key][1] += 1
        if is_win:
            self.global_stats[0] += 1
        else:
            self.global_stats[1] += 1
        self.total_signals += 1

    def get_probability(self, signature: StrategySignature) -> float:
        global_prob = self.global_stats[0] / max(sum(self.global_stats), 1)
        regime_key = signature.regime
        instr_key = signature.instrument_type
        reg_entry = self.regime_stats.get(regime_key, {}).get(instr_key, [1, 4])
        regime_prob = reg_entry[0] / max(sum(reg_entry), 1)
        strat_probs = []
        for i, strat in enumerate(signature.active_strategies):
            key = f"{strat}:{signature.directions[i]}"
            if key in self.strategy_stats:
                a, b = self.strategy_stats[key]
                strat_probs.append(a / max(a + b, 1))
        sorted_pairs = sorted(zip(signature.active_strategies, signature.directions))
        pair_key = str(frozenset(sorted_pairs))
        pair_prob = None
        if pair_key in self.pair_stats:
            a, b = self.pair_stats[pair_key]
            pair_prob = a / max(a + b, 1)
        avg_strat_prob = sum(strat_probs) / max(len(strat_probs), 1) if strat_probs else 0.2
        if pair_prob is not None:
            posterior = (
                0.10 * global_prob + 0.20 * regime_prob + 0.40 * pair_prob + 0.30 * avg_strat_prob
            )
        else:
            posterior = 0.15 * global_prob + 0.25 * regime_prob + 0.60 * avg_strat_prob
        return posterior

    def get_strategy_posterior(self, strategy: str, direction: str) -> float:
        key = f"{strategy}:{direction}"
        if key in self.strategy_stats:
            a, b = self.strategy_stats[key]
            return a / max(a + b, 1)
        return 0.5

    def save(self, path: str = None):
        path = path or self.POSTERIOR_PATH
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {
            "strategy_stats": {k: list(v) for k, v in self.strategy_stats.items()},
            "pair_stats": {k: list(v) for k, v in self.pair_stats.items()},
            "regime_stats": {r: {i: list(v) for i, v in instr.items()} for r, instr in self.regime_stats.items()},
            "global_stats": list(self.global_stats),
            "total_signals": self.total_signals,
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    def load(self, path: str = None):
        path = path or self.POSTERIOR_PATH
        if not os.path.exists(path):
            return
        with open(path) as f:
            data = json.load(f)
        self.strategy_stats = {k: list(v) for k, v in data.get("strategy_stats", {}).items()}
        self.pair_stats = {k: list(v) for k, v in data.get("pair_stats", {}).items()}
        self.regime_stats = {r: {i: list(v) for i, v in instr.items()} for r, instr in data.get("regime_stats", {}).items()}
        self.global_stats = list(data.get("global_stats", [1, 1]))
        self.total_signals = data.get("total_signals", 0)
