from engine.v3.base import BaseV3Strategy

SQ_PARAMS = {
    "short_float_pct_min": 15.0,
    "days_to_cover_min": 3.0,
    "price_up_pct_1d_min": 3.0,
    "volume_ratio_min": 2.0,
    "put_call_oi_ratio_max": 0.7,
    "borrow_rate_min": 20.0,
}


class ShortSqueeze(BaseV3Strategy):
    name = "short_squeeze"
    description = "Short squeeze and gamma squeeze detection"
    applies_to = ("stock",)
    default_weight = 0.07

    def compute(self, context: dict) -> dict:
        m = context.get("metrics", {})
        score = 0.0
        signals = []
        si = m.get("short_float_pct", 0)
        if si >= SQ_PARAMS["short_float_pct_min"]:
            si_score = min(si / 50.0, 1.0) * 30
            score += si_score
            signals.append(f"short_interest={si:.1f}%")
        dtc = m.get("days_to_cover", 0)
        if dtc >= SQ_PARAMS["days_to_cover_min"]:
            dtc_score = min(dtc / 10.0, 1.0) * 20
            score += dtc_score
            signals.append(f"days_to_cover={dtc:.1f}")
        p_up = m.get("price_up_pct_1d", 0)
        if p_up >= SQ_PARAMS["price_up_pct_1d_min"]:
            p_score = min(p_up / 15.0, 1.0) * 20
            score += p_score
            signals.append(f"price_up={p_up:.1f}%")
        vr = m.get("volume_ratio", 1.0)
        if vr >= SQ_PARAMS["volume_ratio_min"]:
            v_score = min(vr / 5.0, 1.0) * 15
            score += v_score
            signals.append(f"vol_ratio={vr:.1f}x")
        pcr = m.get("put_call_oi_ratio", 1.0)
        if pcr <= SQ_PARAMS["put_call_oi_ratio_max"]:
            oi_score = (1.0 - pcr) * 15
            score += oi_score
            signals.append(f"put_call_oi={pcr:.2f}")
        br = m.get("borrow_rate", 0)
        if br >= SQ_PARAMS["borrow_rate_min"]:
            br_bonus = min(br / 100.0, 1.0) * 5
            score += br_bonus
            signals.append(f"borrow_rate={br:.1f}%")
        confidence = min(score / 100.0, 1.0)
        if confidence >= 0.50 and m.get("price_up_pct_1d", 0) > 0:
            return {"direction": "long", "confidence": confidence, "score": score, "signals": signals, "strategy": self.name}
        return {"direction": "neutral", "confidence": 0.0, "score": score, "strategy": self.name}
