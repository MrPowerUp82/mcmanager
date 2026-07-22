import pytest

from mcmanager.console.models import Server, Type
from mcmanager.console.services import process, provisioning
from mcmanager.console.tests.fixtures.fake_java_binary import create_fake_java_binary


@pytest.fixture
def staff_client(client, django_user_model):
    staff = django_user_model.objects.create_user(username="admin", password="pw", is_staff=True)
    client.force_login(staff)
    return client


@pytest.fixture
def provisioned_server(settings, tmp_path):
    settings.JAR_DIR = tmp_path / "jar"
    settings.JAR_DIR.mkdir()
    settings.CONFIGS_DIR = tmp_path / "configs"
    settings.CONFIGS_DIR.mkdir()
    settings.SERVERS_DIR = tmp_path / "servers"
    settings.SERVERS_DIR.mkdir()
    settings.RUN_DIR = tmp_path / "run"
    settings.RUN_DIR.mkdir()
    settings.JAVA_BIN_PATH = str(create_fake_java_binary(tmp_path))
    (settings.JAR_DIR / "paper.jar").write_bytes(b"fake-jar-bytes")
    (settings.CONFIGS_DIR / "server.properties").write_text(
        "server-port=25565\nmotd=Default\n", encoding="utf-8"
    )
    server_type = Type.objects.create(name="Vanilla")
    server = Server.objects.create(name="Test", jar_template="paper.jar", port=25566, type=server_type)
    provisioning.create_server_files(server)
    server.refresh_from_db()
    return server


@pytest.mark.django_db
def test_start_command_stop_full_cycle_through_http(staff_client, provisioned_server):
    server = provisioned_server

    start_resp = staff_client.post(f"/console/start_server/{server.id}")
    assert start_resp.status_code == 200
    assert start_resp.json()["status"] == "success"
    assert process.is_running(server) is True

    command_resp = staff_client.post(f"/console/send_command/{server.id}", {"command": "say hi"})
    assert command_resp.status_code == 200
    assert command_resp.json()["status"] == "success"
    assert "say hi" in command_resp.json()["message"]

    stop_resp = staff_client.post(f"/console/stop_server/{server.id}")
    assert stop_resp.status_code == 200
    assert stop_resp.json()["status"] == "success"
    assert process.is_running(server) is False


@pytest.mark.django_db
def test_force_stop_through_http(staff_client, provisioned_server):
    server = provisioned_server
    staff_client.post(f"/console/start_server/{server.id}")
    assert process.is_running(server) is True

    resp = staff_client.post(f"/console/force_stop_server/{server.id}")

    assert resp.status_code == 200
    assert resp.json()["status"] == "success"
    assert process.is_running(server) is False


@pytest.mark.django_db
def test_get_server_stats_through_http(staff_client, provisioned_server):
    server = provisioned_server
    staff_client.post(f"/console/start_server/{server.id}")
    try:
        resp = staff_client.get(f"/console/get_server_stats/{server.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert "cpu_usage" in body
    finally:
        process.force_stop(server)


@pytest.mark.django_db
def test_start_server_already_running_returns_error(staff_client, provisioned_server):
    server = provisioned_server
    staff_client.post(f"/console/start_server/{server.id}")
    try:
        resp = staff_client.post(f"/console/start_server/{server.id}")
        assert resp.status_code == 409
        assert resp.json()["status"] == "error"
    finally:
        process.force_stop(server)


@pytest.mark.django_db
def test_stop_server_when_not_running_returns_error(staff_client, provisioned_server):
    resp = staff_client.post(f"/console/stop_server/{provisioned_server.id}")
    assert resp.status_code == 409
    assert resp.json()["status"] == "error"


@pytest.mark.django_db
def test_send_command_without_command_param_returns_clean_error(staff_client, provisioned_server):
    server = provisioned_server
    staff_client.post(f"/console/start_server/{server.id}")
    try:
        resp = staff_client.post(f"/console/send_command/{server.id}", {})
        assert resp.status_code == 400
        body = resp.json()
        assert body["status"] == "error"
    finally:
        process.force_stop(server)
