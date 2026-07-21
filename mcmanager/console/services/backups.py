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


class RestoreServerRunningError(Exception):
    """Raised by start_restore() when the server is still running."""


def _validate_backup_filename(filename):
    # A pure string check -- pathlib.Path(x).name is NOT a reliable
    # cross-platform guard here: on POSIX, backslash is an ordinary
    # character, so Path('..\\evil.zip').name returns the string
    # unchanged (still unsafe), while on Windows it strips to something
    # that looks safe. This must reject the same inputs on every platform.
    if (
        not filename
        or filename in ('.', '..')
        or '/' in filename
        or '\\' in filename
        or ':' in filename
        or not filename.endswith('.zip')
    ):
        raise ValueError(f'Invalid backup filename: {filename!r}')


def start_restore(server, filename):
    _validate_backup_filename(filename)
    if process.is_running(server):
        raise RestoreServerRunningError(f'Server {server.id} must be stopped before restoring a backup')

    backup_path = settings.BACKUPS_DIR / f'server_{server.id}' / filename
    if not backup_path.exists():
        raise FileNotFoundError(f'Backup not found: {filename}')

    server_dir = settings.SERVERS_DIR / f'server_{server.id}'
    tmp_dir = Path(tempfile.mkdtemp(dir=str(settings.SERVERS_DIR)))
    old_dir_aside = tmp_dir.with_name(tmp_dir.name + '_old')
    moved_old_dir = False
    try:
        shutil.unpack_archive(str(backup_path), str(tmp_dir), 'zip')
        if server_dir.exists():
            server_dir.rename(old_dir_aside)
            moved_old_dir = True
        tmp_dir.rename(server_dir)
    except Exception:
        # If we already moved the old directory aside but the final swap
        # failed, put it back rather than leaving the server with nothing.
        if moved_old_dir and not server_dir.exists():
            old_dir_aside.rename(server_dir)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    else:
        if moved_old_dir:
            shutil.rmtree(old_dir_aside, ignore_errors=True)


def delete_backup(server, filename):
    _validate_backup_filename(filename)
    backup_path = settings.BACKUPS_DIR / f'server_{server.id}' / filename
    backup_path.unlink(missing_ok=True)
    Backup.objects.filter(server=server, filename=filename).delete()
