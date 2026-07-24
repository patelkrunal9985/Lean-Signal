"""
Lean Signals — Signal Dashboard HTTP Server.
No auth, no position management, no risk gates.
"""
import json
import time
import os
import re
import gzip
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import webbrowser
from utils.logger import get_logger
from engine.runner import run_cycle, get_status, start_auto_run, stop_auto_run, init as init_engine


def _launch_browser(url: str):
    """Open the dashboard in the default browser, best-effort."""
    try:
        webbrowser.open(url, new=0, autoraise=True)
        logger.info(f"Browser launched for {url}")
    except Exception as e:
        logger.debug(f"Could not open browser: {e}")

_VALIDATION_RESULT = None
_VALIDATION_CREATED_CYCLE = 0

logger = get_logger("server")

PROJECT_ROOT = Path(__file__).parent
TEMPLATES_DIR = PROJECT_ROOT / "templates"
STATIC_DIR = PROJECT_ROOT / "static"

_RATE_LIMITS: dict[str, list] = {}
_RATE_LIMIT_WINDOW = 60
_RATE_LIMIT_MAX = 120


def _rate_limited(ip: str) -> bool:
    now = time.time()
    if ip not in _RATE_LIMITS:
        _RATE_LIMITS[ip] = []
    _RATE_LIMITS[ip] = [t for t in _RATE_LIMITS[ip] if now - t < _RATE_LIMIT_WINDOW]
    if len(_RATE_LIMITS[ip]) >= _RATE_LIMIT_MAX:
        return True
    _RATE_LIMITS[ip].append(now)
    return False


_VALIDATION_EXPIRE_AFTER = 10

def _auto_expire_validation():
    global _VALIDATION_RESULT, _VALIDATION_CREATED_CYCLE
    if _VALIDATION_RESULT is None:
        return
    current = get_status().get("cycle_count", 0)
    if current - _VALIDATION_CREATED_CYCLE >= _VALIDATION_EXPIRE_AFTER:
        logger.info(f"Validation result expired (created at cycle {_VALIDATION_CREATED_CYCLE}, now {current})")
        _VALIDATION_RESULT = None
        _VALIDATION_CREATED_CYCLE = 0


def _load_html(name: str) -> str:
    path = TEMPLATES_DIR / name
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _json_response(data: dict) -> tuple[bytes, bool]:
    import math as _math
    # Sanitize NaN/Inf values that break JavaScript JSON.parse()
    def _sanitize(obj):
        if isinstance(obj, dict):
            return {k: _sanitize(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_sanitize(v) for v in obj]
        if isinstance(obj, float) and (_math.isnan(obj) or _math.isinf(obj)):
            return 0.0
        return obj
    data = _sanitize(data)
    body = json.dumps(data, indent=2, default=str).encode("utf-8")
    compressed = len(body) > 1024
    if compressed:
        body = gzip.compress(body)
    return body, compressed


class LeanSignalsHandler(BaseHTTPRequestHandler):

    def _send_json(self, data: dict, status: int = 200):
        body, compressed = _json_response(data)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        if compressed:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_html(self, html: str, status: int = 200):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_static(self, path: str):
        full_path = STATIC_DIR / path
        if not full_path.exists() or ".." in path:
            self._send_json({"error": "not_found"}, 404)
            return
        ext = full_path.suffix.lower()
        mime_map = {
            ".js": "application/javascript",
            ".css": "text/css",
            ".png": "image/png",
            ".svg": "image/svg+xml",
            ".ico": "image/x-icon",
        }
        mime = mime_map.get(ext, "application/octet-stream")
        try:
            body = full_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            # Prevent browser caching so popup fixes take effect immediately
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        ip = self.client_address[0]
        if not self.path.startswith("/static/") and self.path not in ("/", "/dashboard") and _rate_limited(ip) and "/api/" not in self.path:
            self._send_json({"error": "rate_limited"}, 429)
            return

        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        params = parse_qs(parsed.query)

        try:
            if path == "/" or path == "/dashboard" or path == "":
                html = _load_html("dashboard.html")
                if html:
                    self._send_html(html)
                else:
                    self._send_html("<h1>Loading...</h1>")
            elif path.startswith("/static/"):
                self._send_static(path[8:])
            elif path == "/api/status":
                self._send_json(get_status())
            elif path == "/api/run-cycle":
                result = run_cycle()
                self._send_json(result)
            elif path == "/api/auto-run/start":
                start_auto_run()
                self._send_json({"status": "started"})
            elif path == "/api/auto-run/stop":
                stop_auto_run()
                self._send_json({"status": "stopped"})
            elif path == "/api/restart":
                # GET shows instructions; POST triggers the actual restart.
                # The restart daemon kills ONLY the old server PID, clears caches,
                # and starts a fresh instance. Response returns before the kill.
                self._send_json({
                    "status": "info",
                    "message": "Send POST /api/restart to trigger server restart."
                })
            elif path == "/api/validate":
                self._send_json({"status": "hit", "message": "Send POST /api/validate to run validation."})
            elif path == "/api/validate/result":
                _auto_expire_validation()
                if _VALIDATION_RESULT:
                    self._send_json(_VALIDATION_RESULT)
                else:
                    self._send_json({"status": "no_data", "message": "No validation results. POST /api/validate first."})
            elif path == "/api/prices":
                # Lightweight endpoint: live prices only (no cycle data).
                # Safe to poll every 1-2s — reads cache, no IBKR slots burned.
                try:
                    from engine.ibkr_data_feed import get_all_live_prices
                    prices = get_all_live_prices()
                    # Return only the fields the ticker needs: ticker, price, bid, ask, change
                    slim = {}
                    for t, p in prices.items():
                        slim[t] = {
                            "price": p.get("price", 0),
                            "bid": p.get("bid", 0),
                            "ask": p.get("ask", 0),
                            "change": p.get("change_pct", 0),
                            "age": round(p.get("age_seconds", 0), 1),
                        }
                    self._send_json(slim)
                except Exception:
                    self._send_json({})
            elif path == "/api/health":
                self._send_json({"status": "ok", "timestamp": time.time()})
            elif path == "/api/signal-states":
                try:
                    from engine.signal_persistence import get_all_states, get_significant_flips
                    states = get_all_states()
                    flips = get_significant_flips(min_score=0.60)
                    self._send_json({"states": states, "flips": flips})
                except Exception:
                    self._send_json({"states": {"summary": {}}, "flips": {"real": [], "potential": []}})
            elif path == "/api/flip-history":
                try:
                    from engine.signal_persistence import get_flip_history
                    raw_ticker = params.get("ticker", [None])[0] if params.get("ticker") else None
                    ticker_filter = raw_ticker if raw_ticker and raw_ticker.strip() else None
                    raw_score = params.get("min_score", ["0.0"])[0] if params.get("min_score") else "0.0"
                    min_score = float(raw_score)
                    history = get_flip_history(ticker=ticker_filter, min_score=min_score)
                    self._send_json({"flips": history})
                except Exception:
                    self._send_json({"flips": []})
            elif path == "/api/strategy-performance":
                try:
                    from engine.signal_persistence import get_strategy_performance_summary
                    perf = get_strategy_performance_summary()
                    # Sort by win rate descending
                    sorted_perf = sorted(perf.items(), key=lambda x: x[1].get("win_rate", 0), reverse=True)
                    top5 = sorted_perf[:5]
                    bottom5 = sorted_perf[-5:] if len(sorted_perf) > 5 else []
                    self._send_json({
                        "all": {k: v for k, v in sorted_perf},
                        "top5": {k: v for k, v in top5},
                        "bottom5": {k: v for k, v in bottom5},
                    })
                except Exception:
                    self._send_json({"all": {}, "top5": [], "bottom5": []})
            elif path == "/api/signal-timeline":
                try:
                    from engine.signal_persistence import get_signal_timeline
                    raw_ticker = params.get("ticker", [None])[0] if params.get("ticker") else None
                    raw_cycles = params.get("cycles", ["10"])[0] if params.get("cycles") else "10"
                    ticker_filter = raw_ticker if raw_ticker and raw_ticker.strip() else None
                    max_cycles = int(raw_cycles)
                    if ticker_filter:
                        timeline = get_signal_timeline(ticker_filter, max_cycles)
                        self._send_json({"ticker": ticker_filter, "timeline": timeline})
                    else:
                        self._send_json({"error": "ticker parameter required"}, 400)
                except Exception:
                    self._send_json({"error": "failed", "timeline": []})
            elif path == "/api/settings":
                from utils.settings_manager import get_all as get_all_settings
                self._send_json(get_all_settings())
            elif path == "/api/order-flow-ticker":
                try:
                    from engine.tick_engine import get_tick_stats
                    result = {}
                    for t in ["ES=F", "NQ=F", "RTY=F", "YM=F", "CL=F", "GC=F"]:
                        stats = get_tick_stats(t)
                        if stats and stats.get("cumulative_delta", 0) != 0:
                            result[t] = {
                                "cumulative_delta": stats.get("cumulative_delta", 0),
                                "delta_60s": stats.get("delta_60s", 0),
                                "vpin": stats.get("vpin", 0),
                                "last_price": stats.get("last_price", 0),
                            }
                    self._send_json(result)
                except Exception:
                    self._send_json({})
            elif path == "/api/take-profit-events":
                try:
                    from engine.signal_persistence import get_take_profit_events
                    raw_ticker = params.get("ticker", [None])[0] if params.get("ticker") else None
                    ticker_filter = raw_ticker if raw_ticker and raw_ticker.strip() else None
                    events = get_take_profit_events(ticker=ticker_filter)
                    self._send_json({"events": events})
                except Exception:
                    self._send_json({"events": []})
            elif path == "/api/data-quality":
                self._send_json(self._get_data_quality())
            elif path == "/favicon.ico":
                # Browsers auto-request this; return 204 to avoid 404 noise
                self.send_response(204)
                self.end_headers()
            else:
                self._send_json({"error": "not_found"}, 404)
        except Exception as e:
            logger.error(f"GET {path}: {e}")
            self._send_json({"error": str(e)}, 500)

    def _get_data_quality(self) -> dict:
        """Aggregate data quality metrics across all subsystems."""
        result = {
            "ibkr_connected": False,
            "live_tickers": 0,
            "option_chains_active": 0,
            "v2_strategies_loaded": 0,
            "v3_strategies_loaded": 0,
            "last_cycle_errors": 0,
            "signal_states_tracked": 0,
            "health_warnings": 0,
        }
        try:
            from engine.ibkr_connector import is_connected
            result["ibkr_connected"] = is_connected()
        except Exception:
            pass
        try:
            from engine.ibkr_data_feed import get_all_live_prices
            prices = get_all_live_prices()
            result["live_tickers"] = len(prices)
        except Exception:
            pass
        try:
            from engine.v2.registry import V2StrategyRegistry
            r = V2StrategyRegistry()
            result["v2_strategies_loaded"] = len(r._strategies)
        except Exception:
            pass
        try:
            from engine.v3.registry import get_strategies
            v3s = get_strategies("future") + get_strategies("stock") + get_strategies("option")
            result["v3_strategies_loaded"] = len(v3s)
        except Exception:
            pass
        try:
            from engine.signal_persistence import get_all_states, get_all_health_scores
            states = get_all_states()
            result["signal_states_tracked"] = states.get("summary", {}).get("total_tracked", 0)
            health = get_all_health_scores()
            result["health_warnings"] = sum(
                len(h.get("warnings", [])) for h in health.values()
            )
        except Exception:
            pass
        try:
            from engine.runner import _last_cycle_result as lcr
            last = lcr or {}
            result["last_cycle_errors"] = 1 if last.get("status") == "error" else 0
        except Exception:
            pass
        return result

    def do_POST(self):
        ip = self.client_address[0]
        if _rate_limited(ip):
            self._send_json({"error": "rate_limited"}, 429)
            return

        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""
        data = {}
        if body:
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                pass

        try:
            if path == "/api/run-cycle":
                result = run_cycle()
                _auto_expire_validation()
                self._send_json(result)
            elif path == "/api/validate":
                _auto_expire_validation()
                global _VALIDATION_RESULT, _VALIDATION_CREATED_CYCLE
                import subprocess, sys as _sys
                script = str(PROJECT_ROOT / "test_strategies.py")
                if not os.path.exists(script):
                    self._send_json({"status": "error", "message": "test_strategies.py not found"})
                    return
                proc = subprocess.run(
                    [_sys.executable, script, "--json"],
                    capture_output=True, text=True, timeout=120,
                    cwd=str(PROJECT_ROOT),
                )
                stdout = proc.stdout
                start = stdout.find("<<<JSON_START>>>")
                end = stdout.find("<<<JSON_END>>>")
                if start != -1 and end != -1:
                    raw = stdout[start + len("<<<JSON_START>>>"):end].strip()
                    data = json.loads(raw)
                else:
                    data = {"status": "error", "message": "JSON parse failed", "raw_stdout": stdout[:2000], "stderr": proc.stderr[:2000]}
                data["created_at_cycle"] = get_status().get("cycle_count", 0)
                _VALIDATION_RESULT = data
                _VALIDATION_CREATED_CYCLE = data["created_at_cycle"]
                self._send_json(data)
            elif path == "/api/validate/result":
                _auto_expire_validation()
                if _VALIDATION_RESULT:
                    self._send_json(_VALIDATION_RESULT)
                else:
                    self._send_json({"status": "no_data", "message": "No validation results. POST /api/validate first."})
            elif path == "/api/settings":
                from utils.settings_manager import set_many, get_all as get_all_settings
                set_many(data)
                self._send_json(get_all_settings())
            elif path == "/api/restart":
                # Spawn a detached process that kills ONLY the old server PID,
                # clears caches, and starts a fresh instance.
                import subprocess, sys as _sys
                restart_cmd = [
                    _sys.executable, str(PROJECT_ROOT / "scripts" / "restart_daemon.py"),
                    str(os.getpid())
                ]
                subprocess.Popen(
                    restart_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                self._send_json({"status": "restarting", "message": "Server restart triggered. New instance will be up in ~5s."})
            else:
                self._send_json({"error": "not_found"}, 404)
        except Exception as e:
            logger.error(f"POST {path}: {e}")
            self._send_json({"error": str(e)}, 500)

    def log_message(self, format, *args):
        if "/api/status" in str(args[0]) or "/api/health" in str(args[0]):
            return
        logger.info(f"{self.client_address[0]} - {format % args}")


class ThreadedHTTPServer(HTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address, handler_class):
        super().__init__(server_address, handler_class)
        self.executor = ThreadPoolExecutor(max_workers=20)

    def process_request(self, request, client_address):
        self.executor.submit(self.process_request_thread, request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)


def run_server(host: str = "0.0.0.0", port: int = 8088, open_browser: bool = True):
    # Kill any orphaned server on the same port before binding
    try:
        import subprocess, os, signal
        result = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                parts = line.strip().split()
                if parts:
                    pid_str = parts[-1]
                    try:
                        pid = int(pid_str)
                        if pid != os.getpid():
                            if os.name == "nt":
                                os.system(f"taskkill /F /PID {pid} >nul 2>&1")
                            else:
                                os.kill(pid, signal.SIGTERM)
                            logger.warning(f"Killed orphaned server PID {pid} on port {port}")
                    except (ValueError, OSError):
                        pass
    except Exception:
        pass

    logger.info(f"Initializing engine...")
    init_engine()
    server = ThreadedHTTPServer((host, port), LeanSignalsHandler)
    url = f"http://localhost:{port}" if host == "0.0.0.0" else f"http://{host}:{port}"
    logger.info(f"Lean Signals dashboard at {url}")
    if open_browser:
        # Small delay so the server socket is fully bound before browser hits it
        import threading
        threading.Timer(0.5, lambda: _launch_browser(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
        server.server_close()


if __name__ == "__main__":
    run_server()
