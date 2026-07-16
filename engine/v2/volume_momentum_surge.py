"""
Volume Momentum Surge Strategy — Day-trade momentum continuation.
Detects tickers with sudden volume surges + price acceleration for
high-probability continuation trades.
"""
import numpy as np


class VolumeMomentumSurgeStrategy:
    """Day-trade strategy: volume-confirmed momentum surges for continuation."""

    def __init__(self):
        self.name = "volume_momentum_surge"

    async def execute(self, context: dict) -> dict:
        ticker = context.get("ticker", "")
        indicators = context.get("indicators", {})
        ohlcv = context.get("ohlcv", [])

        if not ohlcv or len(ohlcv) < 20:
            return {"ticker": ticker, "direction": "neutral", "confidence": 0.0,
                    "strategy": self.name, "reasons": ["insufficient data"]}

        closes = np.array([c["close"] for c in ohlcv])
        volumes = np.array([c["volume"] for c in ohlcv], dtype=float)

        close = closes[-1]
        volume = volumes[-1]
        avg_vol = np.mean(volumes[-20:]) if len(volumes) >= 20 else np.mean(volumes)
        vol_ratio = volume / max(avg_vol, 0.01)

        roc_3 = (closes[-1] - closes[-4]) / max(closes[-4], 0.01) if len(closes) >= 4 else 0
        roc_5 = (closes[-1] - closes[-6]) / max(closes[-6], 0.01) if len(closes) >= 6 else 0
        roc_10 = (closes[-1] - closes[-11]) / max(closes[-11], 0.01) if len(closes) >= 11 else 0

        roc_accel = roc_3 - roc_10

        atr = indicators.get("atr_14", 0)
        atr_pct = atr / max(close, 0.01) if atr > 0 else 0.015
        adx = indicators.get("adx", 0)
        if adx == 0 and len(closes) >= 15:
            from kronos.utils.helpers import calculate_adx
            highs = np.array([c["high"] for c in ohlcv])
            lows = np.array([c["low"] for c in ohlcv])
            adx = calculate_adx(highs.tolist(), lows.tolist(), closes.tolist())

        macd = indicators.get("macd", {})
        macd_val = macd.get("macd", 0) if isinstance(macd, dict) else 0
        macd_sig = macd.get("signal", 0) if isinstance(macd, dict) else 0
        macd_hist = macd.get("histogram", 0) if isinstance(macd, dict) else 0

        rsi = indicators.get("rsi_14", 50)
        bb = indicators.get("bollinger_bands", {})
        bb_pct = (close - bb.get("lower", close)) / max(bb.get("upper", close) - bb.get("lower", close), 0.01) if bb.get("upper", 0) > 0 else 0.5
        liq = indicators.get("liquidity_sweep") or {}
        liq_detected = liq.get("detected", False)
        liq_type = liq.get("type", "")

        direction = "neutral"
        confidence = 0.0
        reasons = []

        # IEX depth order flow confirmation
        of = indicators.get("order_flow_imbalance", {})
        iex_dir = of.get("direction", "neutral")

        # Long surge: volume spike + upward acceleration + trend confirmation
        if vol_ratio > 1.5 and roc_5 > atr_pct * 0.3 and roc_accel > 0:
            base_conf = min(vol_ratio * abs(roc_5) / max(atr_pct, 0.001) * 0.15, 0.60)
            if adx > 20:
                base_conf = min(base_conf + 0.12, 0.80)
                reasons.append(f"trend_adx={adx:.0f}")
            if macd_val > macd_sig and macd_hist > 0:
                base_conf = min(base_conf + 0.10, 0.85)
                reasons.append("macd_bullish")
            if rsi < 70:
                base_conf = min(base_conf + 0.06, 0.88)
                reasons.append("room_rsi")
            if liq_detected and liq_type == "bullish_liquidity_sweep":
                base_conf = min(base_conf + 0.08, 0.90)
                reasons.append("liq_sweep_confirm")
            if bb_pct < 0.8:
                base_conf = min(base_conf + 0.05, 0.90)
                reasons.append("bb_room")
            if iex_dir == "bullish":
                base_conf = min(base_conf + 0.07, 0.90)
                reasons.append("iex_flow_confirm")
            elif iex_dir == "bearish":
                base_conf = max(base_conf - 0.10, 0.0)
                reasons.append("iex_flow_conflict")
            if base_conf >= 0.25:
                direction = "long"
                confidence = base_conf
                reasons.append(f"vol_surge={vol_ratio:.1f}x_roc={roc_5:.4f}")

        # Short surge: volume spike + downward acceleration + trend confirmation
        elif vol_ratio > 1.5 and roc_5 < -atr_pct * 0.3 and roc_accel < 0:
            base_conf = min(vol_ratio * abs(roc_5) / max(atr_pct, 0.001) * 0.15, 0.60)
            if adx > 20:
                base_conf = min(base_conf + 0.12, 0.80)
                reasons.append(f"trend_adx={adx:.0f}")
            if macd_val < macd_sig and macd_hist < 0:
                base_conf = min(base_conf + 0.10, 0.85)
                reasons.append("macd_bearish")
            if rsi > 30:
                base_conf = min(base_conf + 0.06, 0.88)
                reasons.append("room_rsi")
            if liq_detected and liq_type == "bearish_liquidity_sweep":
                base_conf = min(base_conf + 0.08, 0.90)
                reasons.append("liq_sweep_confirm")
            if bb_pct > 0.2:
                base_conf = min(base_conf + 0.05, 0.90)
                reasons.append("bb_room")
            if iex_dir == "bearish":
                base_conf = min(base_conf + 0.07, 0.90)
                reasons.append("iex_flow_confirm")
            elif iex_dir == "bullish":
                base_conf = max(base_conf - 0.10, 0.0)
                reasons.append("iex_flow_conflict")
            if base_conf >= 0.25:
                direction = "short"
                confidence = base_conf
                reasons.append(f"vol_surge={vol_ratio:.1f}x_roc={roc_5:.4f}")

        if not reasons:
            reasons.append(f"vol_ratio={vol_ratio:.2f}_no_surge")

        return {
            "ticker": ticker,
            "direction": direction,
            "confidence": round(confidence, 4),
            "strategy": self.name,
            "reasons": reasons,
            "vol_ratio": round(vol_ratio, 2),
            "roc_5": round(roc_5, 4),
        }
