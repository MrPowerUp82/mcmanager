import time
from unittest.mock import patch

import pytest

from mcmanager.console.models import Backup, Server, Type
from mcmanager.console.services import backups


def _wait_for_terminal_status(backup_id, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        backup = Backup.objects.get(id=backup_id)
        if backup.status in ('done', 'error'):
            return backup
        time.sleep(0.05)
    raise AssertionError('Backup did not reach a terminal status in time')


@pytest.fixture
def server_type(db):
    return Type.objects.create(name="Vanilla")


@pytest.fixture
def server_with_files(settings, tmp_path, server_type):
    settings.SERVERS_DIR = tmp_path / "servers"
    settings.SERVERS_DIR.mkdir()
    settings.BACKUPS_DIR = tmp_path / "backups"
    settings.BACKUPS_DIR.mkdir()

    server = Server.objects.create(name="Test", jar_template="paper.jar", jar="1_paper.jar", port=25566, type=server_type)
    server_dir = settings.SERVERS_DIR / f"server_{server.id}"
    server_dir.mkdir()
    (server_dir / "world.dat").write_text("fake world data", encoding="utf-8")
    (server_dir / "server.properties").write_text("motd=Test\n", encoding="utf-8")
    return server


@pytest.mark.django_db(transaction=True)
def test_start_backup_zips_server_directory_when_not_running(server_with_files, settings):
    with patch("mcmanager.console.services.backups.process.is_running", return_value=False), \
         patch("mcmanager.console.services.backups.process.send_command") as mock_send:
        backup = backups.start_backup(server_with_files)
        result = _wait_for_terminal_status(backup.id)

    assert result.status == "done"
    assert result.filename.endswith(".zip")
    mock_send.assert_not_called()

    backup_files = backups.list_backups(server_with_files)
    assert backup_files == [result.filename]

    import shutil
    zip_path = settings.BACKUPS_DIR / f"server_{server_with_files.id}" / result.filename
    extracted = settings.BACKUPS_DIR / "extracted"
    shutil.unpack_archive(str(zip_path), str(extracted), "zip")
    assert (extracted / "world.dat").read_text(encoding="utf-8") == "fake world data"


@pytest.mark.django_db(transaction=True)
def test_start_backup_calls_save_off_all_on_in_order_when_running(server_with_files):
    with patch("mcmanager.console.services.backups.process.is_running", return_value=True), \
         patch("mcmanager.console.services.backups.process.send_command") as mock_send:
        backup = backups.start_backup(server_with_files)
        result = _wait_for_terminal_status(backup.id)

    assert result.status == "done"
    calls = [call.args[1] for call in mock_send.call_args_list]
    assert calls == ["save-off", "save-all", "save-on"]


@pytest.mark.django_db(transaction=True)
def test_start_backup_still_sends_save_on_when_zip_fails(server_with_files):
    with patch("mcmanager.console.services.backups.process.is_running", return_value=True), \
         patch("mcmanager.console.services.backups.process.send_command") as mock_send, \
         patch("mcmanager.console.services.backups.shutil.make_archive", side_effect=OSError("disk full")):
        backup = backups.start_backup(server_with_files)
        result = _wait_for_terminal_status(backup.id)

    assert result.status == "error"
    assert "disk full" in result.error_message
    calls = [call.args[1] for call in mock_send.call_args_list]
    assert calls == ["save-off", "save-all", "save-on"]


@pytest.mark.django_db(transaction=True)
def test_retention_keeps_only_5_most_recent_backups(server_with_files, settings):
    backup_dir = settings.BACKUPS_DIR / f"server_{server_with_files.id}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for i in range(5):
        old_name = f"2026010{i}T000000Z.zip"
        (backup_dir / old_name).write_bytes(b"old")
        Backup.objects.create(server=server_with_files, status="done", filename=old_name)

    with patch("mcmanager.console.services.backups.process.is_running", return_value=False), \
         patch("mcmanager.console.services.backups.process.send_command"):
        backup = backups.start_backup(server_with_files)
        result = _wait_for_terminal_status(backup.id)

    assert result.status == "done"
    remaining = backups.list_backups(server_with_files)
    assert len(remaining) == 5
    assert result.filename in remaining
    assert Backup.objects.filter(server=server_with_files).count() == 5
