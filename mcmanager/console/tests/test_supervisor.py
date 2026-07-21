from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from mcmanager.console.models import Server, Type
from mcmanager.console.services import process, provisioning, supervisor
from mcmanager.console.tests.fixtures.fake_java_binary import create_fake_java_binary


@pytest.fixture
def server_type(db):
    return Type.objects.create(name="Vanilla")


@pytest.fixture
def provisioned_server(settings, tmp_path, server_type):
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
    server = Server.objects.create(name="Test", jar_template="paper.jar", port=25566, type=server_type)
    provisioning.create_server_files(server)
    server.refresh_from_db()
    return server


@pytest.mark.django_db
def test_tick_restarts_server_that_crashed_with_desired_running(provisioned_server):
    server = provisioned_server
    server.auto_restart_enabled = True
    server.desired_running = True
    server.save()

    try:
        supervisor._tick()
        assert process.is_running(server) is True
        server.refresh_from_db()
        assert server.consecutive_restart_failures == 0
    finally:
        process.force_stop(server)


@pytest.mark.django_db
def test_tick_does_not_restart_when_desired_running_is_false(provisioned_server):
    server = provisioned_server
    server.auto_restart_enabled = True
    server.desired_running = False
    server.save()

    supervisor._tick()

    assert process.is_running(server) is False


@pytest.mark.django_db
def test_tick_does_not_restart_when_auto_restart_disabled(provisioned_server):
    server = provisioned_server
    server.auto_restart_enabled = False
    server.desired_running = True
    server.save()

    supervisor._tick()

    assert process.is_running(server) is False


@pytest.mark.django_db
def test_tick_resets_failure_counter_when_server_is_running(provisioned_server):
    server = provisioned_server
    server.auto_restart_enabled = True
    server.desired_running = True
    server.consecutive_restart_failures = 2
    server.save()
    process.start(server)

    try:
        supervisor._tick()
        server.refresh_from_db()
        assert server.consecutive_restart_failures == 0
    finally:
        process.force_stop(server)


@pytest.mark.django_db
def test_tick_disables_auto_restart_after_repeated_failures(provisioned_server, settings):
    server = provisioned_server
    server.auto_restart_enabled = True
    server.desired_running = True
    server.save()
    settings.JAVA_BIN_PATH = str(Path("no") / "such" / "java")

    for _ in range(supervisor.MAX_RESTART_ATTEMPTS):
        supervisor._tick()
        server.refresh_from_db()
        assert server.auto_restart_enabled is True

    supervisor._tick()

    server.refresh_from_db()
    assert server.auto_restart_enabled is False


@pytest.mark.django_db
def test_tick_treats_already_running_race_as_success(provisioned_server):
    server = provisioned_server
    server.auto_restart_enabled = True
    server.desired_running = True
    server.consecutive_restart_failures = 2
    server.save()

    with patch("mcmanager.console.services.supervisor.process.is_running", return_value=False), \
         patch(
             "mcmanager.console.services.supervisor.process.start",
             side_effect=process.AlreadyRunningError("already running"),
         ):
        supervisor._tick()

    server.refresh_from_db()
    assert server.consecutive_restart_failures == 0
    assert server.auto_restart_enabled is True


@pytest.mark.django_db
def test_check_scheduled_backup_triggers_when_time_has_passed(provisioned_server):
    server = provisioned_server
    server.scheduled_backup_time = time(0, 0)
    server.save()

    with patch("mcmanager.console.services.supervisor.backups.start_backup") as mock_start:
        supervisor._tick()

    mock_start.assert_called_once_with(server)
    server.refresh_from_db()
    assert server.last_scheduled_backup_date == datetime.now(timezone.utc).date()


@pytest.mark.django_db
def test_check_scheduled_backup_does_not_trigger_before_time(provisioned_server):
    server = provisioned_server
    future_time = (datetime.now(timezone.utc) + timedelta(hours=1)).time()
    server.scheduled_backup_time = future_time
    server.save()

    with patch("mcmanager.console.services.supervisor.backups.start_backup") as mock_start:
        supervisor._tick()

    mock_start.assert_not_called()


@pytest.mark.django_db
def test_check_scheduled_backup_does_not_repeat_same_day(provisioned_server):
    server = provisioned_server
    server.scheduled_backup_time = time(0, 0)
    server.last_scheduled_backup_date = datetime.now(timezone.utc).date()
    server.save()

    with patch("mcmanager.console.services.supervisor.backups.start_backup") as mock_start:
        supervisor._tick()

    mock_start.assert_not_called()


@pytest.mark.django_db
def test_check_scheduled_backup_does_nothing_when_not_set(provisioned_server):
    server = provisioned_server
    assert server.scheduled_backup_time is None

    with patch("mcmanager.console.services.supervisor.backups.start_backup") as mock_start:
        supervisor._tick()

    mock_start.assert_not_called()


@pytest.mark.django_db
def test_tick_continues_to_other_servers_after_one_raises(provisioned_server, settings, server_type):
    # First server: scheduled backup that will raise when start_backup is called.
    provisioned_server.scheduled_backup_time = time(0, 0)
    provisioned_server.save()

    # Second server: crashed, needs restarting.
    second_server = Server.objects.create(
        name="Second", jar_template="paper.jar", port=25567, type=server_type
    )
    provisioning.create_server_files(second_server)
    second_server.refresh_from_db()
    second_server.auto_restart_enabled = True
    second_server.desired_running = True
    second_server.save()

    try:
        with patch(
            "mcmanager.console.services.supervisor.backups.start_backup",
            side_effect=Exception("boom"),
        ):
            supervisor._tick()

        assert process.is_running(second_server) is True
    finally:
        process.force_stop(second_server)
