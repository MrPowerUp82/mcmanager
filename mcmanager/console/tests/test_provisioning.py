import pytest
from django.contrib import admin as django_admin

from mcmanager.console.admin import ServerAdmin
from mcmanager.console.models import Server, Type
from mcmanager.console.services import provisioning


@pytest.fixture
def server_type(db):
    return Type.objects.create(name="Vanilla")


@pytest.fixture
def jar_dir(settings, tmp_path):
    settings.JAR_DIR = tmp_path / "jar"
    settings.JAR_DIR.mkdir()
    (settings.JAR_DIR / "paper.jar").write_bytes(b"fake-jar-bytes")
    return settings.JAR_DIR


@pytest.fixture
def configs_dir(settings, tmp_path):
    settings.CONFIGS_DIR = tmp_path / "configs"
    settings.CONFIGS_DIR.mkdir()
    (settings.CONFIGS_DIR / "server.properties").write_text(
        "server-port=25565\nmotd=Default\n", encoding="utf-8"
    )
    return settings.CONFIGS_DIR


@pytest.fixture
def servers_dir(settings, tmp_path):
    settings.SERVERS_DIR = tmp_path / "servers"
    settings.SERVERS_DIR.mkdir()
    return settings.SERVERS_DIR


@pytest.mark.django_db
def test_create_server_files_provisions_new_server(server_type, jar_dir, configs_dir, servers_dir):
    server = Server.objects.create(
        name="Test", jar_template="paper.jar", port=25566, type=server_type
    )

    provisioning.create_server_files(server)

    server.refresh_from_db()
    server_dir = servers_dir / f"server_{server.id}"
    assert server.jar == f"{server.id}_paper.jar"
    assert (server_dir / server.jar).exists()
    assert (server_dir / "eula.txt").read_text(encoding="utf-8") == "eula=true"
    assert "server-port=25566" in server.server_properties


@pytest.mark.django_db
def test_sync_server_properties_file_writes_field_to_disk(server_type, jar_dir, configs_dir, servers_dir):
    server = Server.objects.create(
        name="Test", jar_template="paper.jar", port=25566, type=server_type
    )
    provisioning.create_server_files(server)

    server.server_properties = "server-port=25566\nmotd=Changed\n"
    provisioning.sync_server_properties_file(server)

    server_dir = servers_dir / f"server_{server.id}"
    on_disk = (server_dir / "server.properties").read_text(encoding="utf-8")
    assert "motd=Changed" in on_disk


@pytest.mark.django_db
def test_delete_server_files_removes_directory(server_type, jar_dir, configs_dir, servers_dir):
    server = Server.objects.create(
        name="Test", jar_template="paper.jar", port=25566, type=server_type
    )
    provisioning.create_server_files(server)
    server_dir = servers_dir / f"server_{server.id}"
    assert server_dir.exists()

    provisioning.delete_server_files(server)

    assert not server_dir.exists()


@pytest.mark.django_db
def test_creating_server_via_model_save_does_not_auto_provision(server_type, jar_dir, configs_dir, servers_dir):
    """Regression guard: plain .save() must not touch the filesystem now that signals are gone."""
    server = Server.objects.create(
        name="Test", jar_template="paper.jar", port=25566, type=server_type
    )
    server_dir = servers_dir / f"server_{server.id}"
    assert not server_dir.exists()


@pytest.mark.django_db
def test_admin_save_model_provisions_new_server(server_type, jar_dir, configs_dir, servers_dir):
    server = Server(name="Test", jar_template="paper.jar", port=25567, type=server_type)
    server_admin = ServerAdmin(Server, django_admin.site)

    server_admin.save_model(request=None, obj=server, form=None, change=False)

    server_dir = servers_dir / f"server_{server.id}"
    assert (server_dir / server.jar).exists()


@pytest.mark.django_db
def test_admin_delete_model_removes_server_directory(server_type, jar_dir, configs_dir, servers_dir):
    server = Server(name="Test", jar_template="paper.jar", port=25568, type=server_type)
    server_admin = ServerAdmin(Server, django_admin.site)
    server_admin.save_model(request=None, obj=server, form=None, change=False)
    server_dir = servers_dir / f"server_{server.id}"
    assert server_dir.exists()

    server_admin.delete_model(request=None, obj=server)

    assert not server_dir.exists()
