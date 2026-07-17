from engine.v3.base import BaseV3Strategy


class FVGLiquiditySweep(BaseV3Strategy):
    name = "fvg_liquidity_sweep"
    description = "Detects Fair Value Gaps and liquidity sweeps for high-probability reversals"
    applies_to = ("future", "stock")
    default_weight = 0.08

    def compute(self, context: dict) -> dict:
        candles_1m = context.get("ohlcv_1m", [])
        if len(candles_1m) < 30:
            return {
                "direction": "neutral", "confidence": 0.0,
                "fvg_score": 0.0, "sweep_score": 0.0,
                "found_liquidity_sweep": False, "found_fvg": False,
                "gap_size_pct": 0.0, "strategy": self.name,
            }

        candles = candles_1m[-30:]
        current_price = context.get("current_price", candles[-1]["close"])

        fvg_score, found_fvg, gap_size_pct, fvg_dir = self._detect_fvg(candles, current_price)

        sweep_score, found_sweep, sweep_dir = self._detect_liquidity_sweep(
            candles, current_price
        )

        confluence_bonus = 0.3 if (found_fvg and found_sweep) else 0.0

        raw_confidence = fvg_score * 0.35 + sweep_score * 0.35 + confluence_bonus * 0.30
        confidence = min(max(raw_confidence, 0.0), 0.85)

        if confidence < 0.01:
            return {
                "direction": "neutral", "confidence": 0.0,
                "fvg_score": round(fvg_score, 4),
                "sweep_score": round(sweep_score, 4),
                "found_liquidity_sweep": found_sweep,
                "found_fvg": found_fvg,
                "gap_size_pct": round(gap_size_pct, 6),
                "strategy": self.name,
            }

        direction = sweep_dir if found_sweep else fvg_dir

        return {
            "direction": direction,
            "confidence": round(confidence, 4),
            "fvg_score": round(fvg_score, 4),
            "sweep_score": round(sweep_score, 4),
            "found_liquidity_sweep": found_sweep,
            "found_fvg": found_fvg,
            "gap_size_pct": round(gap_size_pct, 6),
            "strategy": self.name,
        }

    @staticmethod
    def _round_step(price: float) -> float:
        if price >= 10000:
            return 100.0
        if price >= 1000:
            return 50.0
        if price >= 100:
            return 10.0
        return 5.0

    def _detect_fvg(self, candles: list, current_price: float) -> tuple:
        best_score = 0.0
        best_gap_pct = 0.0
        found = False
        direction = "neutral"

        for i in range(len(candles) - 2):
            a = candles[i]
            b = candles[i + 2]

            if a["low"] > b["high"]:
                gap = a["low"] - b["high"]
                gap_pct = gap / current_price if current_price > 0 else 0
                score = min(gap_pct / 0.002, 1.0)
                if score > best_score:
                    best_score = score
                    best_gap_pct = gap_pct
                    found = True
                    direction = "long"

            if a["high"] < b["low"]:
                gap = b["low"] - a["high"]
                gap_pct = gap / current_price if current_price > 0 else 0
                score = min(gap_pct / 0.002, 1.0)
                if score > best_score:
                    best_score = score
                    best_gap_pct = gap_pct
                    found = True
                    direction = "short"

        return best_score, found, best_gap_pct, direction

    def _detect_liquidity_sweep(self, candles: list, current_price: float) -> tuple:
        if len(candles) < 5:
            return 0.0, False, "neutral"

        lookback = min(len(candles), 20)
        recent = candles[-lookback:]

        highest_high = max(c["high"] for c in recent)
        lowest_low = min(c["low"] for c in recent)

        step = self._round_step(current_price)
        round_ceiling = ((current_price // step) + 1) * step
        round_floor = ((current_price // step) - 1) * step
        if round_floor < 0:
            round_floor = 0.0

        best_score = 0.0
        found = False
        direction = "neutral"

        for i in range(len(candles) - 2):
            breakout = candles[i]
            reject1 = candles[i + 1]
            reject2 = candles[i + 2]

            for level, lev_dir in [(highest_high, "short"), (round_ceiling, "short")]:
                if (breakout["high"] > level
                        and reject1["close"] < level
                        and reject2["close"] < level):
                    dist = (breakout["high"] - level) / current_price
                    score = min(dist / 0.001, 1.0) if dist > 0 else 0.5
                    if score > best_score:
                        best_score = score
                        found = True
                        direction = lev_dir

            for level, lev_dir in [(lowest_low, "long"), (round_floor, "long")]:
                if (breakout["low"] < level
                        and reject1["close"] > level
                        and reject2["close"] > level):
                    dist = (level - breakout["low"]) / current_price
                    score = min(dist / 0.001, 1.0) if dist > 0 else 0.5
                    if score > best_score:
                        best_score = score
                        found = True
                        direction = lev_dir

        return best_score, found, direction
