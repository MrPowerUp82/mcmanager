"""Background supervisor: restarts servers that crashed unexpectedly and
triggers scheduled daily backups. Runs as a single daemon thread started
explicitly by `mcmanager run` (never during migrate/shell/tests)."""
import threading

from ..models import Server
from . import process

TICK_SECONDS = 30
MAX_RESTART_ATTEMPTS = 3

_stop_event = threading.Event()


def start():
    thread = threading.Thread(target=_run_forever, daemon=True)
    thread.start()
    return thread


def _run_forever():
    while not _stop_event.is_set():
        _tick()
        _stop_event.wait(TICK_SECONDS)


def _tick():
    for server in Server.objects.all():
        _check_auto_restart(server)


def _check_auto_restart(server):
    if not server.auto_restart_enabled or not server.desired_running:
        return
    if process.is_running(server):
        if server.consecutive_restart_failures:
            server.consecutive_restart_failures = 0
            server.save(update_fields=['consecutive_restart_failures'])
        return

    if server.consecutive_restart_failures >= MAX_RESTART_ATTEMPTS:
        server.auto_restart_enabled = False
        server.save(update_fields=['auto_restart_enabled'])
        return

    try:
        process.start(server)
    except process.AlreadyRunningError:
        if server.consecutive_restart_failures:
            server.consecutive_restart_failures = 0
            server.save(update_fields=['consecutive_restart_failures'])
        return
    except Exception:
        server.consecutive_restart_failures += 1
        server.save(update_fields=['consecutive_restart_failures'])
        return

    if server.consecutive_restart_failures:
        server.consecutive_restart_failures = 0
        server.save(update_fields=['consecutive_restart_failures'])
