"""Background supervisor: restarts servers that crashed unexpectedly and
triggers scheduled daily backups. Runs as a single daemon thread started
explicitly by `mcmanager run` (never during migrate/shell/tests)."""
import logging
import threading
from datetime import datetime, timezone

from ..models import Server
from . import backups, process

logger = logging.getLogger(__name__)

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
        try:
            _check_auto_restart(server)
            _check_scheduled_backup(server)
        except Exception:
            logger.exception(
                'Unexpected error while checking server %s (%s) in supervisor tick',
                server.id, server.name,
            )


def _check_auto_restart(server):
    if not server.auto_restart_enabled or not server.desired_running:
        return
    if process.is_running(server):
        if server.consecutive_restart_failures:
            server.consecutive_restart_failures = 0
            server.save(update_fields=['consecutive_restart_failures'])
        return

    if server.consecutive_restart_failures >= MAX_RESTART_ATTEMPTS:
        logger.warning(
            'Disabling auto-restart for server %s (%s) after %d consecutive failed restart attempts',
            server.id, server.name, server.consecutive_restart_failures,
        )
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
        logger.warning(
            'Restart attempt failed for server %s (%s), attempt %d of %d',
            server.id, server.name, server.consecutive_restart_failures + 1, MAX_RESTART_ATTEMPTS,
            exc_info=True,
        )
        server.consecutive_restart_failures += 1
        server.save(update_fields=['consecutive_restart_failures'])
        return

    if server.consecutive_restart_failures:
        server.consecutive_restart_failures = 0
        server.save(update_fields=['consecutive_restart_failures'])


def _check_scheduled_backup(server):
    if server.scheduled_backup_time is None:
        return
    now = datetime.now(timezone.utc)
    if server.last_scheduled_backup_date == now.date():
        return
    if now.time() < server.scheduled_backup_time:
        return
    server.last_scheduled_backup_date = now.date()
    server.save(update_fields=['last_scheduled_backup_date'])
    backups.start_backup(server)
