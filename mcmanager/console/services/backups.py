"""Orchestrates server backups: zips the server directory (using RCON
save-off/save-all/save-on to avoid a corrupted world if the server is
running), retains only the most recent backups, and lets a stopped server's
directory be restored from -- or a backup be deleted -- on request."""
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import threading

from django.conf import settings

from ..models import Backup
from . import process

RETENTION_COUNT = 5


def list_backups(server):
    backup_dir = settings.BACKUPS_DIR / f'server_{server.id}'
    if not backup_dir.exists():
        return []
    return sorted((p.name for p in backup_dir.glob('*.zip')), reverse=True)


def start_backup(server):
    backup = Backup.objects.create(server=server, status='pending')
    thread = threading.Thread(target=_run_backup, args=(backup.id,), daemon=True)
    thread.start()
    return backup


def _run_backup(backup_id):
    backup = Backup.objects.get(id=backup_id)
    server = backup.server
    backup.status = 'running'
    backup.save(update_fields=['status'])

    server_dir = settings.SERVERS_DIR / f'server_{server.id}'
    backup_dir = settings.BACKUPS_DIR / f'server_{server.id}'
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    filename = f'{timestamp}.zip'
    dest_path = backup_dir / filename
    server_was_running = process.is_running(server)

    try:
        if server_was_running:
            process.send_command(server, 'save-off')
            process.send_command(server, 'save-all')

        with tempfile.TemporaryDirectory(dir=str(backup_dir)) as tmp_dir:
            archive_path = shutil.make_archive(str(Path(tmp_dir) / timestamp), 'zip', root_dir=str(server_dir))
            shutil.move(archive_path, str(dest_path))

        backup.filename = filename
        backup.status = 'done'
        backup.save(update_fields=['filename', 'status'])
        try:
            _apply_retention(server)
        except Exception:
            pass  # cleanup of OLD backups failing must not mark THIS successful backup as an error
    except Exception as exc:
        backup.status = 'error'
        backup.error_message = str(exc)
        backup.save(update_fields=['status', 'error_message'])
    finally:
        if server_was_running:
            try:
                process.send_command(server, 'save-on')
            except Exception:
                pass


def _apply_retention(server):
    for old_filename in list_backups(server)[RETENTION_COUNT:]:
        (settings.BACKUPS_DIR / f'server_{server.id}' / old_filename).unlink(missing_ok=True)
        Backup.objects.filter(server=server, filename=old_filename).delete()
