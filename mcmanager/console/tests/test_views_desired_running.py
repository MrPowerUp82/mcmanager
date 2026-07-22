import pytest

from mcmanager.console.models import Server
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
    from mcmanager.console.models import Type as TypeModel
    server_type = TypeModel.objects.create(name="Vanilla")
    server = Server.objects.create(name="Test", jar_template="paper.jar", port=25566, type=server_type)
    provisioning.create_server_files(server)
    server.refresh_from_db()
    return server


@pytest.mark.django_db
def test_start_server_sets_desired_running_true_and_resets_failures(staff_client, provisioned_server):
    server = provisioned_server
    server.consecutive_restart_failures = 2
    server.save()

    try:
        resp = staff_client.post(f"/console/start_server/{server.id}")
        assert resp.status_code == 200
        server.refresh_from_db()
        assert server.desired_running is True
        assert server.consecutive_restart_failures == 0
    finally:
        process.force_stop(server)


@pytest.mark.django_db
def test_stop_server_sets_desired_running_false(staff_client, provisioned_server):
    server = provisioned_server
    staff_client.post(f"/console/start_server/{server.id}")

    resp = staff_client.post(f"/console/stop_server/{server.id}")

    assert resp.status_code == 200
    server.refresh_from_db()
    assert server.desired_running is False


@pytest.mark.django_db
def test_force_stop_server_sets_desired_running_false(staff_client, provisioned_server):
    server = provisioned_server
    staff_client.post(f"/console/start_server/{server.id}")

    resp = staff_client.post(f"/console/force_stop_server/{server.id}")

    assert resp.status_code == 200
    server.refresh_from_db()
    assert server.desired_running is False
