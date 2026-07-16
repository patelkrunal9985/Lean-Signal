#!/bin/bash
# Lean Signals — Server Restart Script
# Kills any running server, clears bytecode caches, starts fresh, and verifies.
# Usage: bash scripts/restart.sh
set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

PORT="${1:-8088}"
echo "=== Lean Signals Restart ==="
echo "Project: $PROJECT_DIR"
echo "Port: $PORT"

# 1. Kill existing server processes
echo "[1/4] Stopping existing server..."
taskkill /F /IM python.exe 2>/dev/null || true
sleep 2

# 2. Clear bytecode caches (prevents stale .pyc bugs)
echo "[2/4] Clearing bytecode caches..."
find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find . -name "*.pyc" -type f -delete 2>/dev/null || true
echo "  Caches cleared"

# 3. Start the server
echo "[3/4] Starting server..."
nohup python server.py > /tmp/lean_signals_server.log 2>&1 &
SERVER_PID=$!
echo "  Server PID: $SERVER_PID"

# 4. Wait for it to come up
echo "[4/4] Waiting for server to be ready..."
for i in $(seq 1 30); do
    if curl -s "http://localhost:$PORT/api/health" > /dev/null 2>&1; then
        echo ""
        echo "✅ Server is UP!"
        echo "   Dashboard: http://localhost:$PORT"
        echo "   API:       http://localhost:$PORT/api/status"
        echo "   PID:       $SERVER_PID"
        echo "   Logs:      /tmp/lean_signals_server.log"
        exit 0
    fi
    sleep 2
done

echo ""
echo "⚠️  Server may still be starting. Check logs: tail -f /tmp/lean_signals_server.log"
echo "   PID: $SERVER_PID"
exit 1
