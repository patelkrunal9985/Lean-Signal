"""
Lean Signals — Restart Daemon
Spawned by /api/restart endpoint. Receives the old server's PID as a
command-line arg, waits for the HTTP response to flush, kills ONLY the
old server process, then starts a fresh one. This avoids the kill-self
race condition that happens with blanket process-name killing.
"""
import os
import sys
import time
import signal
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent


def main():
    if len(sys.argv) < 2:
        print("Usage: restart_daemon.py <old_server_pid>", file=sys.stderr)
        sys.exit(1)

    old_pid = int(sys.argv[1])

    # Give the HTTP response 2 seconds to flush back to the client
    time.sleep(2)

    # Kill ONLY the old server process (not ourselves!)
    if sys.platform == "win32":
        os.system(f"taskkill /F /PID {old_pid} >nul 2>&1")
    else:
        try:
            os.kill(old_pid, signal.SIGTERM)
        except ProcessLookupError:
            pass  # Already dead

    time.sleep(1)

    # Clear bytecode caches so new server starts with fresh code
    for root, dirs, files in os.walk(PROJECT_ROOT):
        if "__pycache__" in dirs:
            import shutil
            shutil.rmtree(os.path.join(root, "__pycache__"), ignore_errors=True)
        for f in files:
            if f.endswith(".pyc"):
                try:
                    os.remove(os.path.join(root, f))
                except OSError:
                    pass

    # Start the new server
    server_py = PROJECT_ROOT / "server.py"
    subprocess.Popen(
        [sys.executable, str(server_py)],
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    print(f"Server restarted at {time.strftime('%H:%M:%S')}")


if __name__ == "__main__":
    main()
