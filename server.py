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

from utils.logger import get_logger
from engine.runner import run_cycle, get_status, start_auto_run, stop_auto_run, init as init_engine

_VALIDATION_RESULT = None
_VALIDATION_CREATED_CYCLE = 0

logger = get_logger("server")

PROJECT_ROOT = Path(__file__).parent
TEMPLATES_DIR = PROJECT_ROOT / "templates"
STATIC_DIR = PROJECT_ROOT / "static"

HTML_CACHE = {}

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
    if name in HTML_CACHE:
        return HTML_CACHE[name]
    path = TEMPLATES_DIR / name
    if path.exists():
        content = path.read_text(encoding="utf-8")
        HTML_CACHE[name] = content
        return content
    return ""


def _json_response(data: dict) -> tuple[bytes, bool]:
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
        if _rate_limited(ip) and "/api/" not in self.path:
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
            elif path == "/api/health":
                self._send_json({"status": "ok", "timestamp": time.time()})
            else:
                self._send_json({"error": "not_found"}, 404)
        except Exception as e:
            logger.error(f"GET {path}: {e}")
            self._send_json({"error": str(e)}, 500)

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


def run_server(host: str = "0.0.0.0", port: int = 8088):
    logger.info(f"Initializing engine...")
    init_engine()
    server = ThreadedHTTPServer((host, port), LeanSignalsHandler)
    logger.info(f"Lean Signals dashboard at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
        server.server_close()


if __name__ == "__main__":
    run_server()
