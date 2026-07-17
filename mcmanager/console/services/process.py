"""Cross-platform process lifecycle management for Minecraft server
processes. Replaces the Phase 1 pty.fork()/`/tmp` PID-file approach with
subprocess.Popen and a JSON state file under ~/.mcmanager/run/, so it works
on both Linux and Windows and survives the panel process restarting."""
import json
import os
import subprocess
from datetime import datetime, timezone

import psutil
from django.conf import settings


class AlreadyRunningError(Exception):
    """Raised by start() when the server is already running."""


class ProcessNotRunningError(Exception):
    """Raised by stop()/send_command()/get_stats() when the server isn't running."""


class JavaNotFoundError(Exception):
    """Raised by start() when the configured Java binary can't be executed."""


class StopTimeoutError(Exception):
    """Raised by stop() when the process doesn't exit within the graceful-stop timeout."""


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
    return state.get('jar', '') in cmdline


def start(server):
    if is_running(server):
        raise AlreadyRunningError(f'Server {server.id} is already running')

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
                proc.kill()
                proc.wait(timeout=5)
            except psutil.NoSuchProcess:
                pass
            except psutil.TimeoutExpired:
                pass
    _clear_state(server)


def get_stats(server):
    state = _read_state(server)
    if state is None or not is_running(server):
        raise ProcessNotRunningError(f'Server {server.id} is not running')
    process_handle = psutil.Process(state['pid'])
    cpu_usage = process_handle.cpu_percent(interval=1)
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
