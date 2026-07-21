"""
Lean Signals — Server Restart
Finds the server process by port, kills only it, clears caches, starts fresh.
Usage: python scripts/restart_server.py [port]

Unlike restart.bat, this script kills ONLY the server process (not all Python).
"""
import os
import sys
import time
import subprocess
import socket
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8088


def find_pid_on_port(port: int) -> int | None:
    """Find the PID of the process listening on the given port."""
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ["netstat", "-ano"], capture_output=True, text=True, timeout=10,
            )
            for line in result.stdout.split("\n"):
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.strip().split()
                    if parts:
                        return int(parts[-1])
        else:
            result = subprocess.run(
                ["lsof", "-ti", f":{port}"], capture_output=True, text=True, timeout=5,
            )
            if result.stdout.strip():
                return int(result.stdout.strip().split("\n")[0])
    except Exception as e:
        print(f"  Could not find PID on port {port}: {e}")
    return None


def kill_pid(pid: int) -> bool:
    """Kill a process by PID."""
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=10)
        else:
            subprocess.run(["kill", "-9", str(pid)], capture_output=True, timeout=5)
        print(f"  Killed PID {pid}")
        return True
    except Exception as e:
        print(f"  Failed to kill PID {pid}: {e}")
        return False


def clear_caches():
    """Clear Python bytecode caches."""
    count = 0
    for root, dirs, files in os.walk(PROJECT_ROOT):
        if "__pycache__" in dirs:
            import shutil
            shutil.rmtree(os.path.join(root, "__pycache__"), ignore_errors=True)
            count += 1
        for f in files:
            if f.endswith(".pyc"):
                try:
                    os.remove(os.path.join(root, f))
                    count += 1
                except OSError:
                    pass
    print(f"  Cleared {count} cache items")


def wait_for_server(timeout: int = 30) -> bool:
    """Wait for the server to accept connections on its port."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex(("127.0.0.1", PORT))
            sock.close()
            if result == 0:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def main():
    print(f"=== Lean Signals Restart (Port {PORT}) ===")
    print(f"Project: {PROJECT_ROOT}")

    # 1. Find and kill the server process
    print("[1/4] Stopping server...")
    pid = find_pid_on_port(PORT)
    if pid:
        kill_pid(pid)
        time.sleep(2)
    else:
        print(f"  No process found on port {PORT}")

    # 2. Clear caches
    print("[2/4] Clearing bytecode caches...")
    clear_caches()

    # 3. Start new server
    print("[3/4] Starting server...")
    server_py = str(PROJECT_ROOT / "server.py")
    log_stdout = str(PROJECT_ROOT / "live_stdout.txt")
    log_stderr = str(PROJECT_ROOT / "live_stderr.txt")

    with open(log_stdout, "w") as out, open(log_stderr, "w") as err:
        subprocess.Popen(
            [sys.executable, server_py],
            cwd=str(PROJECT_ROOT),
            stdout=out,
            stderr=err,
        )
    print("  Server launched")

    # 4. Wait for ready
    print("[4/4] Waiting for server...")
    if wait_for_server():
        print(f"\n  Server is UP!")
        print(f"  Dashboard: http://localhost:{PORT}")
    else:
        print(f"\n  Server may still be starting — check http://localhost:{PORT}/api/health")


if __name__ == "__main__":
    main()
