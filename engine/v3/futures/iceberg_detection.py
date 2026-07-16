from kronos.strategies.v3.base import BaseV3Strategy


class IcebergDetection(BaseV3Strategy):
    name = "iceberg_detection"
    description = "Hidden iceberg order detection via L2 depth decay, tick clusters, and volume concentration"
    applies_to = ("future",)
    default_weight = 0.06

    def compute(self, context: dict) -> dict:
        ticker = context.get("ticker", "")
        ohlcv = context.get("ohlcv", [])
        candles_1m = context.get("candles_1m", [])
        ticks = context.get("ticks", [])
        depth_decay = 0.0
        iceberg_price_level = 0.0
        tick_cluster_count = 0
        source = "none"
        if ticker:
            try:
                from kronos.skills.ibkr_data_feed import get_market_depth
                depth = get_market_depth(ticker)
                if depth:
                    bids = depth.get("bids", [])
                    asks = depth.get("asks", [])
                    if len(bids) >= 4 and len(asks) >= 4:
                        top_ask_vol = sum(a[1] for a in asks[:3])
                        deep_ask_vol = sum(a[1] for a in asks[3:10])
                        total_ask_vol = top_ask_vol + deep_ask_vol
                        if total_ask_vol > 0:
                            depth_decay = top_ask_vol / max(total_ask_vol, 1)
                        else:
                            depth_decay = 0.0
                        if depth_decay < 0.3 and deep_ask_vol > top_ask_vol * 2:
                            iceberg_price_level = asks[0][0]
                            source = "iex_ask"
                        top_bid_vol = sum(b[1] for b in bids[:3])
                        deep_bid_vol = sum(b[1] for b in bids[3:10])
                        total_bid_vol = top_bid_vol + deep_bid_vol
                        bid_decay = 0.0
                        if total_bid_vol > 0:
                            bid_decay = top_bid_vol / max(total_bid_vol, 1)
                        if bid_decay < 0.3 and deep_bid_vol > top_bid_vol * 2:
                            if iceberg_price_level == 0 or bid_decay < depth_decay:
                                iceberg_price_level = bids[0][0]
                                source = "iex_bid"
                                depth_decay = bid_decay
            except Exception:
                pass
        if source == "none" and ticks:
            try:
                clusters = self._find_tick_clusters(ticks)
                if clusters:
                    cluster_prices = sorted(clusters.items(), key=lambda x: x[1], reverse=True)
                    top_price, top_count = cluster_prices[0]
                    if top_count >= 3 and len(cluster_prices) > 1:
                        iceberg_price_level = top_price
                        tick_cluster_count = top_count
                        source = "tick_clusters"
                        depth_decay = 1.0 - (top_count / max(sum(c for _, c in cluster_prices[:5]), 1))
            except Exception:
                pass
        if source == "none" and len(candles_1m) >= 10:
            vol_concentration = self._check_volume_concentration(candles_1m)
            if vol_concentration["concentrated"]:
                iceberg_price_level = vol_concentration["price_level"]
                depth_decay = vol_concentration["decay"]
                source = "ohlcv_proxy"
        if source == "none":
            return {"direction": "neutral", "confidence": 0.0,
                    "depth_decay": 0.0, "iceberg_price_level": 0.0,
                    "tick_cluster_count": 0, "source": "none",
                    "strategy": self.name}
        current = 0.0
        if ohlcv:
            current = ohlcv[-1]["close"]
        elif candles_1m:
            current = candles_1m[-1]["close"]
        long_conf = 0.0
        short_conf = 0.0
        if "bid" in source and iceberg_price_level > 0 and current > 0:
            short_conf = min(depth_decay * 0.60, 0.40)
        elif "ask" in source and iceberg_price_level > 0 and current > 0:
            long_conf = min(depth_decay * 0.60, 0.40)
        elif source == "tick_clusters":
            if ticker:
                try:
                    from kronos.skills.ibkr_data_feed import get_market_quote
                    quote = get_market_quote(ticker)
                    qp = quote.get("price", 0)
                    if qp > 0 and iceberg_price_level > 0:
                        if qp > iceberg_price_level:
                            long_conf = 0.30
                        else:
                            short_conf = 0.30
                except Exception:
                    pass
        elif source == "ohlcv_proxy":
            ohlcv_prices = [c["close"] for c in candles_1m[-5:]]
            if ohlcv_prices:
                avg_price = sum(ohlcv_prices) / len(ohlcv_prices)
                if avg_price > 0 and iceberg_price_level > 0:
                    if avg_price > iceberg_price_level:
                        short_conf = 0.25
                    else:
                        long_conf = 0.25
        tick_cluster_count = len(
            [t for t in (ticks or []) if abs(t.get("price", 0) - iceberg_price_level) < 0.05]
        ) if ticks and iceberg_price_level > 0 else 0
        if long_conf >= short_conf and long_conf > 0.10:
            return {"direction": "long", "confidence": round(long_conf, 4),
                    "depth_decay": round(depth_decay, 4),
                    "iceberg_price_level": round(iceberg_price_level, 2),
                    "tick_cluster_count": tick_cluster_count, "source": source,
                    "strategy": self.name}
        elif short_conf > 0.10:
            return {"direction": "short", "confidence": round(short_conf, 4),
                    "depth_decay": round(depth_decay, 4),
                    "iceberg_price_level": round(iceberg_price_level, 2),
                    "tick_cluster_count": tick_cluster_count, "source": source,
                    "strategy": self.name}
        return {"direction": "neutral", "confidence": 0.0,
                "depth_decay": round(depth_decay, 4),
                "iceberg_price_level": round(iceberg_price_level, 2),
                "tick_cluster_count": tick_cluster_count, "source": source,
                "strategy": self.name}

    @staticmethod
    def _find_tick_clusters(ticks: list) -> dict:
        clusters = {}
        for t in ticks:
            price = t.get("price", 0)
            if price <= 0:
                continue
            rounded = round(price, 2)
            clusters[rounded] = clusters.get(rounded, 0) + 1
        return clusters

    @staticmethod
    def _check_volume_concentration(candles_1m: list) -> dict:
        if len(candles_1m) < 10:
            return {"concentrated": False, "price_level": 0.0, "decay": 0.0}
        price_vol_map = {}
        for c in candles_1m[-30:]:
            close = round(c.get("close", 0), 2)
            vol = c.get("volume", 0)
            if close > 0 and vol > 0:
                price_vol_map[close] = price_vol_map.get(close, 0) + vol
        if not price_vol_map:
            return {"concentrated": False, "price_level": 0.0, "decay": 0.0}
        sorted_levels = sorted(price_vol_map.items(), key=lambda x: x[1], reverse=True)
        top_level, top_vol = sorted_levels[0]
        total_vol = sum(v for _, v in sorted_levels)
        share = top_vol / max(total_vol, 1)
        if share > 0.25 and len(sorted_levels) > 1:
            return {"concentrated": True, "price_level": top_level, "decay": share}
        return {"concentrated": False, "price_level": 0.0, "decay": 0.0}
