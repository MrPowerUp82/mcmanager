from unittest.mock import patch

import pytest

from mcmanager.console.models import JarDownload
from mcmanager.console.services.jar_providers.base import VersionInfo


@pytest.fixture
def staff_client(client, django_user_model):
    staff = django_user_model.objects.create_user(username="admin", password="pw", is_staff=True)
    client.force_login(staff)
    return client


@pytest.mark.django_db
def test_jars_page_requires_staff_login(client):
    resp = client.get("/console/jars/")
    assert resp.status_code == 302


@pytest.mark.django_db
def test_jars_page_loads_for_staff(staff_client):
    resp = staff_client.get("/console/jars/")
    assert resp.status_code == 200


@pytest.mark.django_db
def test_list_jar_versions_returns_versions_from_provider(staff_client):
    with patch(
        "mcmanager.console.views_jars.jars.list_versions",
        return_value=[VersionInfo("1.20.4", "1.20.4")],
    ):
        resp = staff_client.get("/console/jars/versions/mojang")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["versions"] == [{"version": "1.20.4", "label": "1.20.4"}]


@pytest.mark.django_db
def test_list_jar_versions_returns_clean_error_on_provider_failure(staff_client):
    with patch(
        "mcmanager.console.views_jars.jars.list_versions",
        side_effect=RuntimeError("network down"),
    ):
        resp = staff_client.get("/console/jars/versions/mojang")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "error"


@pytest.mark.django_db
def test_start_jar_download_creates_job_and_returns_id(staff_client):
    with patch("mcmanager.console.views_jars.jars.start_download") as mock_start:
        mock_start.return_value = JarDownload.objects.create(provider="mojang", version="1.20.4")

        resp = staff_client.post("/console/jars/download", {"provider": "mojang", "version": "1.20.4"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert "download_id" in body
    mock_start.assert_called_once_with("mojang", "1.20.4")


@pytest.mark.django_db
def test_start_jar_download_requires_post(staff_client):
    resp = staff_client.get("/console/jars/download")
    assert resp.status_code == 405


@pytest.mark.django_db
def test_start_jar_download_rejects_unknown_provider(staff_client):
    resp = staff_client.post("/console/jars/download", {"provider": "bogus", "version": "1.20.4"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "error"


@pytest.mark.django_db
def test_jar_download_status_returns_current_state(staff_client):
    download = JarDownload.objects.create(
        provider="paper", version="1.20.4", status="done", filename="paper-1.20.4-451.jar"
    )

    resp = staff_client.get(f"/console/jars/download/{download.id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["download_status"] == "done"
    assert body["filename"] == "paper-1.20.4-451.jar"


@pytest.mark.django_db
def test_jar_download_status_returns_error_for_unknown_id(staff_client):
    resp = staff_client.get("/console/jars/download/999999")
    assert resp.status_code == 200
    assert resp.json()["status"] == "error"
