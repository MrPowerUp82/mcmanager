import pytest
from django.core.exceptions import ValidationError

from mcmanager.console.models import Server, Type


@pytest.fixture
def server_type(db):
    return Type.objects.create(name="Vanilla")


@pytest.mark.django_db
def test_clean_rejects_port_too_high_for_rcon_offset(server_type):
    server = Server(name="Test", jar_template="paper.jar", port=55536, type=server_type)

    with pytest.raises(ValidationError) as exc_info:
        server.full_clean()

    assert 'port' in exc_info.value.message_dict


@pytest.mark.django_db
def test_clean_allows_max_valid_port(server_type):
    server = Server(name="Test", jar_template="paper.jar", port=55535, type=server_type)
    # Should not raise for the port-range check (no other servers exist to collide with).
    server.full_clean()


@pytest.mark.django_db
def test_clean_rejects_rcon_port_collision_with_existing_server_port(server_type):
    # First server's RCON port will be 25565 + 10000 = 35565.
    first = Server.objects.create(name="First", jar_template="paper.jar", port=25565, type=server_type)
    first.rcon_port = first.port + 10000
    first.save()

    # Second server's game port collides with the first server's RCON port.
    second = Server(name="Second", jar_template="paper.jar", port=35565, type=server_type)

    with pytest.raises(ValidationError) as exc_info:
        second.full_clean()

    assert 'port' in exc_info.value.message_dict


@pytest.mark.django_db
def test_clean_rejects_implied_rcon_port_collision_with_existing_server(server_type):
    # First server occupies port 45565, which will become another server's implied RCON port.
    first = Server.objects.create(name="First", jar_template="paper.jar", port=45565, type=server_type)

    # Second server's implied RCON port (35565 + 10000 = 45565) collides with the first server's port.
    second = Server(name="Second", jar_template="paper.jar", port=35565, type=server_type)

    with pytest.raises(ValidationError) as exc_info:
        second.full_clean()

    assert 'port' in exc_info.value.message_dict


@pytest.mark.django_db
def test_clean_allows_non_colliding_ports(server_type):
    Server.objects.create(name="First", jar_template="paper.jar", port=25565, type=server_type)
    second = Server(name="Second", jar_template="paper.jar", port=25575, type=server_type)

    second.full_clean()


from mcmanager.console.models import JarDownload


@pytest.mark.django_db
def test_jar_download_defaults_to_pending_status():
    download = JarDownload.objects.create(provider="mojang", version="1.20.4")

    assert download.status == "pending"
    assert download.filename == ""
    assert download.error_message == ""


@pytest.mark.django_db
def test_jar_download_str_includes_provider_and_version():
    download = JarDownload.objects.create(provider="paper", version="1.20.4")

    assert "paper" in str(download)
    assert "1.20.4" in str(download)


from mcmanager.console.models import Backup


@pytest.mark.django_db
def test_backup_defaults_to_pending_status(server_type):
    server = Server.objects.create(name="Test", jar_template="paper.jar", port=25566, type=server_type)
    backup = Backup.objects.create(server=server)

    assert backup.status == "pending"
    assert backup.filename == ""
    assert backup.error_message == ""


@pytest.mark.django_db
def test_backup_deleted_when_server_is_deleted(server_type):
    server = Server.objects.create(name="Test", jar_template="paper.jar", port=25566, type=server_type)
    backup = Backup.objects.create(server=server)

    server.delete()

    assert not Backup.objects.filter(id=backup.id).exists()
