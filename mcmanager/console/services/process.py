"""Cross-platform process lifecycle management for Minecraft server
processes. Replaces the Phase 1 pty.fork()/`/tmp` PID-file approach with
subprocess.Popen and a JSON state file under ~/.mcmanager/run/, so it works
on both Linux and Windows and survives the panel process restarting."""
import json
import os
import socket
import subprocess
from datetime import datetime, timezone

import psutil
from django.conf import settings

from . import rcon


class AlreadyRunningError(Exception):
    """Raised by start() when the server is already running."""


class ProcessNotRunningError(Exception):
    """Raised by stop()/send_command()/get_stats() when the server isn't running."""


class JavaNotFoundError(Exception):
    """Raised by start() when the configured Java binary can't be executed."""


class StopTimeoutError(Exception):
    """Raised by stop() when the process doesn't exit within the graceful-stop timeout."""


class PortInUseError(Exception):
    """Raised by start() when the server's configured port is already bound
    by something else on this machine."""


class JarMissingError(Exception):
    """Raised by start() when the server's configured jar file is missing from disk."""


def _state_path(server):
    return settings.RUN_DIR / f'server_{server.id}.json'


def _read_state(server):
    path = _state_path(server)
    if not path.exists():
        return None
    try:
        with path.open('r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _write_state(server, pid):
    state = {
        'pid': pid,
        'started_at': datetime.now(timezone.utc).isoformat(),
        'jar': server.jar,
    }
    with _state_path(server).open('w', encoding='utf-8') as f:
        json.dump(state, f)


def _clear_state(server):
    path = _state_path(server)
    if path.exists():
        path.unlink()


def is_running(server):
    state = _read_state(server)
    if state is None:
        return False
    pid = state.get('pid')
    if pid is None or not psutil.pid_exists(pid):
        return False
    try:
        process_handle = psutil.Process(pid)
        cmdline = ' '.join(process_handle.cmdline())
    except psutil.NoSuchProcess:
        return False
    return (state.get('jar') or '') in cmdline


def _check_port_available(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        if os.name == 'posix':
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(('0.0.0.0', port))
        except OSError as exc:
            raise PortInUseError(f'Port {port} is already in use') from exc


def _jar_path(server):
    return settings.SERVERS_DIR / f'server_{server.id}' / server.jar


def is_jar_missing(server):
    if not server.jar:
        return True
    return not _jar_path(server).exists()


def start(server):
    if is_running(server):
        raise AlreadyRunningError(f'Server {server.id} is already running')

    _check_port_available(server.port)

    if is_jar_missing(server):
        raise JarMissingError(f'Jar file missing for server {server.id}: {_jar_path(server)}')

    server_dir = settings.SERVERS_DIR / f'server_{server.id}'
    cmd = [
        settings.JAVA_BIN_PATH,
        f'-Xms{server.memory_limit}M',
        f'-Xmx{server.memory_limit}M',
        '-jar', server.jar,
        'nogui',
    ]
    kwargs = {'cwd': str(server_dir), 'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}
    if os.name == 'posix':
        kwargs['start_new_session'] = True
    else:
        kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP

    try:
        proc = subprocess.Popen(cmd, **kwargs)
    except FileNotFoundError as exc:
        raise JavaNotFoundError(f'Java binary not found: {settings.JAVA_BIN_PATH}') from exc

    _write_state(server, proc.pid)


def force_stop(server):
    state = _read_state(server)
    if state is not None:
        pid = state.get('pid')
        if pid is not None and psutil.pid_exists(pid):
            try:
                proc = psutil.Process(pid)
                # Kill the whole descendant tree, not just the tracked PID.
                # A direct `java.exe` launch has no children, so this is a
                # no-op in production. It matters whenever the tracked
                # process is itself a launcher that spawns a real child
                # (e.g. a `.bat` wrapper, as used by this test suite's fake
                # Java binary on Windows): killing only the parent leaves
                # the child running and orphaned, since Windows has no
                # equivalent of POSIX's start_new_session-based group kill.
                children = proc.children(recursive=True)
                procs = children + [proc]
                for p in procs:
                    try:
                        p.kill()
                    except psutil.NoSuchProcess:
                        pass
                psutil.wait_procs(procs, timeout=5)
            except psutil.NoSuchProcess:
                pass
    _clear_state(server)


def get_stats(server, cpu_interval=1):
    state = _read_state(server)
    if state is None or not is_running(server):
        raise ProcessNotRunningError(f'Server {server.id} is not running')
    try:
        process_handle = psutil.Process(state['pid'])
        cpu_usage = process_handle.cpu_percent(interval=cpu_interval)
        memory_info = process_handle.memory_info()
        memory_usage = memory_info.rss / (1024 * 1024)
        virtual_memory = psutil.virtual_memory()
        return {
            'cpu_usage': cpu_usage,
            'memory_usage': memory_usage,
            'total_memory': virtual_memory.total / (1024 * 1024),
            'used_memory': virtual_memory.used / (1024 * 1024),
            'total_cpu_usage': psutil.cpu_percent(interval=1),
        }
    except psutil.NoSuchProcess as exc:
        raise ProcessNotRunningError(f'Server {server.id} is not running') from exc


STOP_TIMEOUT_SECONDS = 30


def stop(server):
    if not is_running(server):
        raise ProcessNotRunningError(f'Server {server.id} is not running')
    state = _read_state(server)
    pid = state['pid']

    rcon.execute('127.0.0.1', server.rcon_port, server.rcon_password, 'stop', timeout=rcon.DEFAULT_TIMEOUT)

    try:
        psutil.Process(pid).wait(timeout=STOP_TIMEOUT_SECONDS)
    except psutil.TimeoutExpired as exc:
        raise StopTimeoutError(
            f'Server {server.id} did not stop within {STOP_TIMEOUT_SECONDS}s of the RCON stop '
            'command; use force-stop instead'
        ) from exc
    except psutil.NoSuchProcess:
        pass
    _clear_state(server)


def send_command(server, command):
    if not is_running(server):
        raise ProcessNotRunningError(f'Server {server.id} is not running')
    return rcon.execute('127.0.0.1', server.rcon_port, server.rcon_password, command)
