import itertools
import os
import time
from pathlib import Path
from unittest.mock import patch

import psutil
import pytest

from mcmanager.console.models import Server, Type
from mcmanager.console.services import process, provisioning
from mcmanager.console.tests.fixtures.fake_java_binary import create_fake_java_binary

_port_counter = itertools.count(25566)


@pytest.fixture
def server_type(db):
    return Type.objects.create(name="Vanilla")


@pytest.fixture
def server_dirs(settings, tmp_path):
    settings.JAR_DIR = tmp_path / "jar"
    settings.JAR_DIR.mkdir()
    settings.CONFIGS_DIR = tmp_path / "configs"
    settings.CONFIGS_DIR.mkdir()
    settings.SERVERS_DIR = tmp_path / "servers"
    settings.SERVERS_DIR.mkdir()
    settings.RUN_DIR = tmp_path / "run"
    settings.RUN_DIR.mkdir()
    (settings.JAR_DIR / "paper.jar").write_bytes(b"fake-jar-bytes")
    (settings.CONFIGS_DIR / "server.properties").write_text(
        "server-port=25565\nmotd=Default\n", encoding="utf-8"
    )
    return tmp_path


@pytest.fixture
def fake_java(settings, tmp_path):
    settings.JAVA_BIN_PATH = str(create_fake_java_binary(tmp_path))


@pytest.fixture
def server(server_type, server_dirs):
    s = Server.objects.create(name="Test", jar_template="paper.jar", port=next(_port_counter), type=server_type)
    provisioning.create_server_files(s)
    s.refresh_from_db()
    return s


def _wait_for_exit(pid, timeout=3):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not psutil.pid_exists(pid):
            return True
        time.sleep(0.1)
    return False


@pytest.mark.django_db
def test_start_writes_state_file_and_marks_running(server, fake_java):
    process.start(server)
    try:
        assert process.is_running(server) is True
        state = process._read_state(server)
        assert state["jar"] == server.jar
        assert psutil.pid_exists(state["pid"])
    finally:
        process.force_stop(server)


@pytest.mark.django_db
def test_start_raises_when_already_running(server, fake_java):
    process.start(server)
    try:
        with pytest.raises(process.AlreadyRunningError):
            process.start(server)
    finally:
        process.force_stop(server)


@pytest.mark.django_db
def test_start_raises_java_not_found_error(server, settings):
    settings.JAVA_BIN_PATH = str(Path("no") / "such" / "java")
    with pytest.raises(process.JavaNotFoundError):
        process.start(server)


@pytest.mark.django_db
def test_is_running_false_when_no_state_file(server):
    assert process.is_running(server) is False


@pytest.mark.django_db
def test_is_running_false_when_pid_belongs_to_unrelated_process(server):
    process._write_state(server, os.getpid())
    assert process.is_running(server) is False


@pytest.mark.django_db
def test_force_stop_kills_process_and_clears_state(server, fake_java):
    process.start(server)
    state = process._read_state(server)
    pid = state["pid"]

    process.force_stop(server)

    assert process.is_running(server) is False
    assert _wait_for_exit(pid)
    assert process._read_state(server) is None


@pytest.mark.django_db
def test_get_stats_returns_cpu_and_memory(server, fake_java):
    process.start(server)
    try:
        stats = process.get_stats(server)
        assert stats["cpu_usage"] >= 0
        assert stats["memory_usage"] > 0
    finally:
        process.force_stop(server)


@pytest.mark.django_db
def test_get_stats_passes_custom_cpu_interval_to_psutil(server, fake_java):
    process.start(server)
    try:
        with patch("mcmanager.console.services.process.psutil.Process") as mock_process_cls:
            mock_instance = mock_process_cls.return_value
            mock_instance.cpu_percent.return_value = 12.5
            mock_instance.memory_info.return_value.rss = 1024 * 1024 * 50
            mock_instance.cmdline.return_value = ['java', '-jar', server.jar]
            stats = process.get_stats(server, cpu_interval=0.2)
            mock_instance.cpu_percent.assert_called_once_with(interval=0.2)
            assert stats["cpu_usage"] == 12.5
    finally:
        process.force_stop(server)


@pytest.mark.django_db
def test_get_stats_raises_when_not_running(server):
    with pytest.raises(process.ProcessNotRunningError):
        process.get_stats(server)


@pytest.mark.django_db
def test_get_stats_maps_no_such_process_race_to_process_not_running_error(server, fake_java):
    """Regression test for the is_running()/psutil.Process() TOCTOU race: if the
    process dies between the is_running() check and the stats read, get_stats()
    must raise ProcessNotRunningError instead of letting psutil.NoSuchProcess
    propagate uncaught (which would 500 the view)."""
    process.start(server)
    try:
        with patch("mcmanager.console.services.process.is_running", return_value=True), \
             patch("mcmanager.console.services.process.psutil.Process") as mock_process_cls:
            mock_process_cls.side_effect = psutil.NoSuchProcess(pid=999999)
            with pytest.raises(process.ProcessNotRunningError):
                process.get_stats(server)
    finally:
        process.force_stop(server)


@pytest.mark.django_db
def test_stop_sends_rcon_stop_and_waits_for_exit(server, fake_java):
    process.start(server)
    assert process.is_running(server) is True

    process.stop(server)

    assert process.is_running(server) is False
    assert process._read_state(server) is None


@pytest.mark.django_db
def test_stop_raises_when_not_running(server):
    with pytest.raises(process.ProcessNotRunningError):
        process.stop(server)


@pytest.mark.django_db
def test_send_command_returns_rcon_response(server, fake_java):
    process.start(server)
    try:
        response = process.send_command(server, "say hi")
        assert "Unknown command: say hi" in response
    finally:
        process.force_stop(server)


@pytest.mark.django_db
def test_send_command_raises_when_not_running(server):
    with pytest.raises(process.ProcessNotRunningError):
        process.send_command(server, "say hi")
