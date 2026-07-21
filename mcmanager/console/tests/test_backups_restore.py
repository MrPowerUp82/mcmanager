import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from mcmanager.console.models import Backup, Server, Type
from mcmanager.console.services import backups


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


@pytest.mark.django_db
def test_start_restore_replaces_server_directory_with_backup_contents(server_with_files, settings):
    backup_dir = settings.BACKUPS_DIR / f"server_{server_with_files.id}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    server_dir = settings.SERVERS_DIR / f"server_{server_with_files.id}"

    archive_path = shutil.make_archive(str(settings.BACKUPS_DIR / "staging"), "zip", root_dir=str(server_dir))
    filename = "20260101T000000Z.zip"
    shutil.move(archive_path, str(backup_dir / filename))

    (server_dir / "world.dat").write_text("MODIFIED AFTER BACKUP", encoding="utf-8")

    with patch("mcmanager.console.services.backups.process.is_running", return_value=False):
        backups.start_restore(server_with_files, filename)

    assert (server_dir / "world.dat").read_text(encoding="utf-8") == "fake world data"


@pytest.mark.django_db
def test_start_restore_raises_when_server_is_running(server_with_files):
    with patch("mcmanager.console.services.backups.process.is_running", return_value=True):
        with pytest.raises(backups.RestoreServerRunningError):
            backups.start_restore(server_with_files, "20260101T000000Z.zip")


@pytest.mark.django_db
def test_start_restore_raises_when_backup_file_missing(server_with_files):
    with patch("mcmanager.console.services.backups.process.is_running", return_value=False):
        with pytest.raises(FileNotFoundError):
            backups.start_restore(server_with_files, "does-not-exist.zip")


@pytest.mark.django_db
def test_start_restore_rolls_back_when_final_swap_fails(server_with_files, settings):
    backup_dir = settings.BACKUPS_DIR / f"server_{server_with_files.id}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    server_dir = settings.SERVERS_DIR / f"server_{server_with_files.id}"

    archive_path = shutil.make_archive(str(settings.BACKUPS_DIR / "staging"), "zip", root_dir=str(server_dir))
    filename = "20260101T000000Z.zip"
    shutil.move(archive_path, str(backup_dir / filename))

    real_rename = Path.rename

    def flaky_rename(self, target):
        # Only fail the final tmp_dir -> server_dir swap (the extracted
        # directory doesn't end in "_old"); let the old_dir_aside rollback
        # rename (which also targets server_dir) succeed normally.
        if Path(target) == server_dir and not str(self).endswith("_old"):
            raise OSError("simulated rename failure")
        return real_rename(self, target)

    with patch("mcmanager.console.services.backups.process.is_running", return_value=False):
        with patch("pathlib.Path.rename", flaky_rename):
            with pytest.raises(OSError):
                backups.start_restore(server_with_files, filename)

    assert (server_dir / "world.dat").read_text(encoding="utf-8") == "fake world data"
    assert (server_dir / "server.properties").exists()


@pytest.mark.django_db
@pytest.mark.parametrize("unsafe_filename", ["../evil.zip", "subdir/evil.zip", "..\\evil.zip", "..", ".", "D:evil.zip"])
def test_start_restore_rejects_unsafe_filenames(server_with_files, unsafe_filename):
    with patch("mcmanager.console.services.backups.process.is_running", return_value=False):
        with pytest.raises(ValueError):
            backups.start_restore(server_with_files, unsafe_filename)


@pytest.mark.django_db
def test_delete_backup_removes_file_and_record(server_with_files, settings):
    backup_dir = settings.BACKUPS_DIR / f"server_{server_with_files.id}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    filename = "20260101T000000Z.zip"
    (backup_dir / filename).write_bytes(b"fake zip content")
    Backup.objects.create(server=server_with_files, status="done", filename=filename)

    backups.delete_backup(server_with_files, filename)

    assert not (backup_dir / filename).exists()
    assert not Backup.objects.filter(server=server_with_files, filename=filename).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("unsafe_filename", ["../evil.zip", "subdir/evil.zip", "..\\evil.zip", "..", ".", "D:evil.zip"])
def test_delete_backup_rejects_unsafe_filenames(server_with_files, unsafe_filename):
    with pytest.raises(ValueError):
        backups.delete_backup(server_with_files, unsafe_filename)
