"""
IBKR Connection Manager — checks connection, manages lifecycle.
If IBKR is not connected, no signal generation happens.
"""
import time
import threading
from utils.logger import get_logger

logger = get_logger("engine.ibkr_connector")

_reconnect_thread = None
_stop_reconnect = threading.Event()


def is_connected() -> bool:
    try:
        from engine.ibkr_data_feed import _connected
        return _connected
    except Exception:
        return False


def connected_since() -> float:
    try:
        from engine.ibkr_data_feed import _connected_at
        return _connected_at
    except Exception:
        return 0.0


def get_connection_status() -> dict:
    conn = is_connected()
    return {
        "connected": conn,
        "connected_since": connected_since() if conn else 0,
        "timestamp": time.time(),
    }


def start_monitoring(interval: float = 5.0):
    global _reconnect_thread, _stop_reconnect
    _stop_reconnect.clear()
    _reconnect_thread = threading.Thread(target=_monitor_loop, args=(interval,), daemon=True)
    _reconnect_thread.start()
    logger.info(f"IBKR monitoring started (interval={interval}s)")


def stop_monitoring():
    _stop_reconnect.set()
    logger.info("IBKR monitoring stopped")


def _monitor_loop(interval: float):
    while not _stop_reconnect.is_set():
        try:
            conn = is_connected()
            if conn:
                logger.debug("IBKR connected")
            else:
                logger.warning("IBKR not connected")
        except Exception:
            pass
        time.sleep(interval)
