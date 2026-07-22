import zipfile
from unittest.mock import patch

import pytest

from mcmanager.console.models import Backup, Server, Type
from mcmanager.console.services import backups


@pytest.fixture
def staff_client(client, django_user_model):
    staff = django_user_model.objects.create_user(username="admin", password="pw", is_staff=True)
    client.force_login(staff)
    return client


@pytest.fixture
def server_type(db):
    return Type.objects.create(name="Vanilla")


@pytest.fixture
def server(server_type):
    return Server.objects.create(name="Test", jar_template="paper.jar", jar="1_paper.jar", port=25566, type=server_type)


@pytest.mark.django_db
def test_list_backups_view_requires_staff_login(client, server):
    resp = client.get(f"/console/backups/{server.id}")
    assert resp.status_code == 302


@pytest.mark.django_db
def test_list_backups_view_returns_backups(staff_client, server):
    with patch("mcmanager.console.views_backups.backups.list_backups", return_value=["20260101T000000Z.zip"]):
        resp = staff_client.get(f"/console/backups/{server.id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["backups"] == ["20260101T000000Z.zip"]


@pytest.mark.django_db
def test_start_backup_view_creates_job(staff_client, server):
    with patch("mcmanager.console.views_backups.backups.start_backup") as mock_start:
        mock_start.return_value = Backup.objects.create(server=server, status="pending")
        resp = staff_client.post(f"/console/backups/{server.id}/create")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert "backup_id" in body
    mock_start.assert_called_once_with(server)


@pytest.mark.django_db
def test_start_backup_view_requires_post(staff_client, server):
    resp = staff_client.get(f"/console/backups/{server.id}/create")
    assert resp.status_code == 405


@pytest.mark.django_db
def test_backup_status_view_returns_current_state(staff_client, server):
    backup = Backup.objects.create(server=server, status="done", filename="20260101T000000Z.zip")

    resp = staff_client.get(f"/console/backups/status/{backup.id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["backup_status"] == "done"
    assert body["filename"] == "20260101T000000Z.zip"


@pytest.mark.django_db
def test_backup_status_view_returns_error_for_unknown_id(staff_client):
    resp = staff_client.get("/console/backups/status/999999")
    assert resp.status_code == 404
    assert resp.json()["status"] == "error"


@pytest.mark.django_db
def test_restore_backup_view_success(staff_client, server):
    with patch("mcmanager.console.views_backups.backups.start_restore") as mock_restore:
        resp = staff_client.post(f"/console/backups/{server.id}/restore", {"filename": "20260101T000000Z.zip"})

    assert resp.status_code == 200
    assert resp.json()["status"] == "success"
    mock_restore.assert_called_once_with(server, "20260101T000000Z.zip")


@pytest.mark.django_db
def test_restore_backup_view_rejects_when_server_running(staff_client, server):
    with patch(
        "mcmanager.console.views_backups.backups.start_restore",
        side_effect=backups.RestoreServerRunningError("running"),
    ):
        resp = staff_client.post(f"/console/backups/{server.id}/restore", {"filename": "20260101T000000Z.zip"})

    assert resp.status_code == 409
    assert resp.json()["status"] == "error"


@pytest.mark.django_db
def test_restore_backup_view_handles_corrupt_archive(staff_client, server):
    with patch(
        "mcmanager.console.views_backups.backups.start_restore",
        side_effect=zipfile.BadZipFile("corrupt archive"),
    ):
        resp = staff_client.post(f"/console/backups/{server.id}/restore", {"filename": "20260101T000000Z.zip"})

    assert resp.status_code == 409
    body = resp.json()
    assert body["status"] == "error"
    assert "corrupt archive" in body["message"]


@pytest.mark.django_db
def test_restore_backup_view_requires_post(staff_client, server):
    resp = staff_client.get(f"/console/backups/{server.id}/restore")
    assert resp.status_code == 405


@pytest.mark.django_db
def test_delete_backup_view_removes_backup(staff_client, server):
    with patch("mcmanager.console.views_backups.backups.delete_backup") as mock_delete:
        resp = staff_client.post(f"/console/backups/{server.id}/delete", {"filename": "20260101T000000Z.zip"})

    assert resp.status_code == 200
    assert resp.json()["status"] == "success"
    mock_delete.assert_called_once_with(server, "20260101T000000Z.zip")


@pytest.mark.django_db
def test_delete_backup_view_handles_os_error(staff_client, server):
    with patch(
        "mcmanager.console.views_backups.backups.delete_backup",
        side_effect=OSError("disk full"),
    ):
        resp = staff_client.post(f"/console/backups/{server.id}/delete", {"filename": "20260101T000000Z.zip"})

    assert resp.status_code == 409
    body = resp.json()
    assert body["status"] == "error"
    assert "disk full" in body["message"]


@pytest.mark.django_db
def test_delete_backup_view_requires_post(staff_client, server):
    resp = staff_client.get(f"/console/backups/{server.id}/delete")
    assert resp.status_code == 405
