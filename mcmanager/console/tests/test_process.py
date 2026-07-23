import itertools
import os
import socket
import stat
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


@pytest.mark.django_db
def test_start_raises_port_in_use_error_when_port_is_occupied(server, fake_java):
    blocking_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocking_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocking_socket.bind(('0.0.0.0', server.port))
    blocking_socket.listen(1)
    try:
        with pytest.raises(process.PortInUseError):
            process.start(server)
        assert process.is_running(server) is False
    finally:
        blocking_socket.close()


@pytest.mark.django_db
def test_is_jar_missing_returns_false_when_jar_file_exists(server, fake_java):
    assert process.is_jar_missing(server) is False


@pytest.mark.django_db
def test_is_jar_missing_returns_true_when_jar_file_deleted(settings, server, fake_java):
    jar_path = settings.SERVERS_DIR / f'server_{server.id}' / server.jar
    jar_path.unlink()
    assert process.is_jar_missing(server) is True


@pytest.mark.django_db
def test_is_jar_missing_returns_true_when_server_has_no_jar_assigned(server_type):
    unprovisioned = Server.objects.create(name="Unprovisioned", jar_template="paper.jar", type=server_type)
    assert process.is_jar_missing(unprovisioned) is True


@pytest.mark.django_db
def test_start_raises_jar_missing_error_when_jar_file_deleted(settings, server, fake_java):
    jar_path = settings.SERVERS_DIR / f'server_{server.id}' / server.jar
    jar_path.unlink()
    with pytest.raises(process.JarMissingError):
        process.start(server)


def _create_crashing_binary(tmp_path):
    """A fake `java` binary that writes a recognizable message to stderr and
    exits with a non-zero code immediately, standing in for a real JVM crash
    (e.g. UnsupportedClassVersionError) without needing a real Java runtime."""
    if os.name == "posix":
        wrapper = tmp_path / "crashing_java"
        wrapper.write_text(
            '#!/bin/sh\necho "FAKE JAVA ERROR: test crash message" >&2\nexit 1\n',
            encoding="utf-8",
        )
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    else:
        wrapper = tmp_path / "crashing_java.bat"
        wrapper.write_text(
            "@echo off\r\necho FAKE JAVA ERROR: test crash message 1>&2\r\nexit /b 1\r\n",
            encoding="utf-8",
        )
    return wrapper


@pytest.mark.django_db
def test_start_creates_logs_directory_if_missing(settings, server, fake_java):
    server_dir = settings.SERVERS_DIR / f"server_{server.id}"
    logs_dir = server_dir / "logs"
    assert not logs_dir.exists()
    try:
        process.start(server)
        assert logs_dir.exists()
    finally:
        process.force_stop(server)


@pytest.mark.django_db
def test_read_launch_output_returns_none_when_never_started(server):
    assert process.read_launch_output(server) is None


@pytest.mark.django_db
def test_start_captures_crash_output_to_file(settings, server, tmp_path):
    settings.JAVA_BIN_PATH = str(_create_crashing_binary(tmp_path))
    process.start(server)
    deadline = time.time() + 3
    output = None
    while time.time() < deadline:
        output = process.read_launch_output(server)
        if output and "FAKE JAVA ERROR" in output:
            break
        time.sleep(0.1)
    assert output is not None
    assert "FAKE JAVA ERROR: test crash message" in output


@pytest.mark.django_db
def test_start_overwrites_previous_launch_output(settings, server, tmp_path):
    settings.JAVA_BIN_PATH = str(_create_crashing_binary(tmp_path))
    process.start(server)
    deadline = time.time() + 3
    while time.time() < deadline:
        output = process.read_launch_output(server)
        if output and "FAKE JAVA ERROR" in output:
            break
        time.sleep(0.1)

    process.start(server)
    deadline = time.time() + 3
    output = None
    while time.time() < deadline:
        output = process.read_launch_output(server)
        if output and output.count("FAKE JAVA ERROR") >= 1:
            break
        time.sleep(0.1)
    assert output.count("FAKE JAVA ERROR: test crash message") == 1
