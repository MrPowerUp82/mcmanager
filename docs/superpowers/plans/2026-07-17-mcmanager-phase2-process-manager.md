# mcmanager Phase 2 — Cross-Platform Process Manager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace mcmanager's Linux-only `pty.fork()` + `/tmp` PID-file process handling with a cross-platform `services/process.py` built on `subprocess.Popen` and a self-implemented RCON client, unblocking Windows support and removing the last shell/PTY-shaped attack surface.

**Architecture:** A new `services/rcon.py` (pure Source RCON protocol client, no Django knowledge) and `services/process.py` (orchestration: builds the Java command line, manages a JSON state file per server, calls into `rcon.py` using RCON credentials stored on the `Server` model) replace all process-handling code currently inline in `views.py`. `views.py` becomes thin wrappers translating `process.py`/`rcon.py` exceptions into the existing JSON contract.

**Tech Stack:** Django 5.1, `psutil` (existing dependency), Python's stdlib `socket`/`struct`/`subprocess` (no new runtime dependencies).

## Global Constraints

- Python `>=3.8`, Django `>=4.2.0,<5.2.0` — from `pyproject.toml`; unchanged this phase.
- No new runtime dependencies. RCON client is self-implemented (~80-100 lines), not a third-party package.
- Scale target: 1 machine, ~1–10 servers. No connection pooling, no per-server-configurable timeouts (YAGNI).
- Cross-platform from this phase forward: every task's tests must pass on both Windows and Linux (no more `@pytest.mark.skipif(os.name != "posix", ...)` — that escape hatch was Phase 1-only, scoped to the code this phase replaces).
- RCON: fixed 5s timeout (`rcon.DEFAULT_TIMEOUT`), bind `127.0.0.1` only, one TCP connection per call (no persistence).
- `process.stop()` never auto-escalates to a kill on timeout — the caller must explicitly force-stop.
- Existing URL names and the `{status, message, ...}` JSON response contract must not change — this phase only changes what happens *inside* each view.
- After every task, `python manage.py check` must pass and the full test suite (all prior tasks' tests plus this task's) must be green.

---

## File Structure

```
mcmanager/
├── settings.py                          # Modify (Task 3): + RUN_DIR
├── cli.py                                # Modify (Task 3): `init` creates run/ dir
└── console/
    ├── models.py                         # Modify (Task 2): + rcon_port/rcon_password; Modify (Task 6): - status
    ├── admin.py                          # Modify (Task 6): list_display/list_filter/readonly_fields
    ├── apps.py                           # Modify (Task 6): remove status-sync ready() loop
    ├── views.py                          # Modify (Task 5): full rewrite of process-handling internals
    ├── services/
    │   ├── rcon.py                       # Create (Task 1)
    │   ├── process.py                    # Create (Task 3), extend (Task 4)
    │   └── provisioning.py               # Modify (Task 2): generate + write RCON credentials
    ├── migrations/
    │   ├── 0006_*.py                     # Create (Task 2): AddField rcon_port/rcon_password + backfill
    │   └── 0007_*.py                     # Create (Task 6): RemoveField status
    ├── templates/
    │   ├── console/index.html            # Modify (Task 6): server.status → server_running
    │   └── index.html                    # Modify (Task 6): per-server live status
    └── tests/
        ├── fixtures/
        │   ├── __init__.py               # Create (Task 1)
        │   ├── fake_rcon_server.py       # Create (Task 1)
        │   ├── fake_java_binary.py       # Create (Task 3)
        │   └── fake_server.py            # Create (Task 3) — includes RCON serving from the start, reused by Task 4
        ├── test_rcon.py                  # Create (Task 1)
        ├── test_process.py               # Create (Task 3), extend (Task 4)
        ├── test_views_start_stop_e2e.py  # Create (Task 5)
        └── test_force_stop.py            # Delete (Task 5): superseded by test_process.py
```

---

### Task 1: RCON protocol client

**Files:**
- Create: `mcmanager/console/services/rcon.py`
- Create: `mcmanager/console/tests/fixtures/__init__.py`
- Create: `mcmanager/console/tests/fixtures/fake_rcon_server.py`
- Create: `mcmanager/console/tests/test_rcon.py`

**Interfaces:**
- Consumes: nothing (pure protocol module, no Django/Server knowledge).
- Produces: `rcon.execute(host: str, port: int, password: str, command: str, timeout: float = rcon.DEFAULT_TIMEOUT) -> str`, `rcon.DEFAULT_TIMEOUT` (float, `5.0`), exceptions `rcon.RconError` (base), `rcon.RconAuthError`, `rcon.RconConnectionError`, `rcon.RconTimeoutError`. Task 4's `process.stop()`/`send_command()` and Task 5's `views.py` both import and use these names exactly.

- [ ] **Step 1: Write the failing tests**

Create `mcmanager/console/tests/fixtures/__init__.py` (empty file).

Create `mcmanager/console/tests/fixtures/fake_rcon_server.py`:

```python
import socket
import struct
import threading


def _recv_exact(sock, n):
    chunks = []
    remaining = n
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            return b''.join(chunks)
        chunks.append(chunk)
        remaining -= len(chunk)
    return b''.join(chunks)


def _read_packet(sock):
    raw_len = _recv_exact(sock, 4)
    if len(raw_len) < 4:
        return None
    length = struct.unpack('<i', raw_len)[0]
    body = _recv_exact(sock, length)
    request_id, packet_type = struct.unpack('<ii', body[:8])
    payload = body[8:-2]
    return request_id, packet_type, payload


def _send_packet(sock, request_id, packet_type, payload):
    body = struct.pack('<ii', request_id, packet_type) + payload + b'\x00\x00'
    sock.sendall(struct.pack('<i', len(body)) + body)


class FakeRconServer:
    """A minimal RCON server for tests: accepts connections, checks the
    configured password, and echoes back a canned response per command."""

    def __init__(self, password, responses=None):
        self.password = password
        self.responses = responses or {}
        self.received_commands = []
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(('127.0.0.1', 0))
        self._sock.listen(1)
        self.port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve_forever, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._sock.close()

    def _serve_forever(self):
        while True:
            try:
                conn, _addr = self._sock.accept()
            except OSError:
                return
            self._handle_connection(conn)

    def _handle_connection(self, conn):
        with conn:
            packet = _read_packet(conn)
            if packet is None:
                return
            request_id, _packet_type, payload = packet
            if payload.decode('utf-8', errors='replace') != self.password:
                _send_packet(conn, -1, 2, b'')
                return
            _send_packet(conn, request_id, 2, b'')
            while True:
                packet = _read_packet(conn)
                if packet is None:
                    return
                request_id, _packet_type, payload = packet
                command = payload.decode('utf-8', errors='replace')
                self.received_commands.append(command)
                response = self.responses.get(command, '')
                _send_packet(conn, request_id, 0, response.encode('utf-8'))
```

Create `mcmanager/console/tests/test_rcon.py`:

```python
import socket
import threading

import pytest

from mcmanager.console.services import rcon
from mcmanager.console.tests.fixtures.fake_rcon_server import FakeRconServer


@pytest.fixture
def fake_rcon_server():
    server = FakeRconServer(password="secret123", responses={"say hi": "Said: hi"})
    server.start()
    yield server
    server.stop()


def test_execute_returns_command_response(fake_rcon_server):
    response = rcon.execute("127.0.0.1", fake_rcon_server.port, "secret123", "say hi")
    assert response == "Said: hi"
    assert fake_rcon_server.received_commands == ["say hi"]


def test_execute_raises_auth_error_on_wrong_password(fake_rcon_server):
    with pytest.raises(rcon.RconAuthError):
        rcon.execute("127.0.0.1", fake_rcon_server.port, "wrong-password", "say hi")


def test_execute_raises_connection_error_when_nothing_listening():
    with pytest.raises(rcon.RconConnectionError):
        rcon.execute("127.0.0.1", 1, "secret123", "say hi", timeout=1.0)


def test_execute_raises_timeout_error_when_server_never_responds():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def accept_and_hang():
        try:
            listener.accept()
        except OSError:
            pass

    thread = threading.Thread(target=accept_and_hang, daemon=True)
    thread.start()

    try:
        with pytest.raises(rcon.RconTimeoutError):
            rcon.execute("127.0.0.1", port, "secret123", "say hi", timeout=0.5)
    finally:
        listener.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest mcmanager/console/tests/test_rcon.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcmanager.console.services.rcon'`

- [ ] **Step 3: Implement the RCON client**

Create `mcmanager/console/services/rcon.py`:

```python
"""Self-implemented Source RCON protocol client, used to send commands to and
gracefully stop a running Minecraft server. No Django or Server model
knowledge lives here — it's a pure network protocol module."""
import socket
import struct

DEFAULT_TIMEOUT = 5.0

_SERVERDATA_AUTH = 3
_SERVERDATA_EXECCOMMAND = 2


class RconError(Exception):
    """Base class for RCON errors."""


class RconAuthError(RconError):
    """Raised when RCON authentication fails (wrong password)."""


class RconConnectionError(RconError):
    """Raised when the RCON socket can't be opened or the connection drops."""


class RconTimeoutError(RconError):
    """Raised when the RCON server doesn't respond within the timeout."""


def _recv_exact(sock, n):
    chunks = []
    remaining = n
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise RconConnectionError("RCON connection closed unexpectedly")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b''.join(chunks)


def _read_packet(sock):
    raw_len = _recv_exact(sock, 4)
    length = struct.unpack('<i', raw_len)[0]
    body = _recv_exact(sock, length)
    request_id, packet_type = struct.unpack('<ii', body[:8])
    payload = body[8:-2]
    return request_id, packet_type, payload


def _send_packet(sock, packet_type, payload):
    request_id = 1
    body = struct.pack('<ii', request_id, packet_type) + payload + b'\x00\x00'
    sock.sendall(struct.pack('<i', len(body)) + body)


def execute(host: str, port: int, password: str, command: str, timeout: float = DEFAULT_TIMEOUT) -> str:
    """Opens a new RCON connection, authenticates, sends `command`, and
    returns the server's response text. Raises RconAuthError,
    RconConnectionError, or RconTimeoutError on failure."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            _send_packet(sock, _SERVERDATA_AUTH, password.encode('utf-8'))
            request_id, _packet_type, _payload = _read_packet(sock)
            if request_id == -1:
                raise RconAuthError("RCON authentication failed")

            _send_packet(sock, _SERVERDATA_EXECCOMMAND, command.encode('utf-8'))
            _request_id, _packet_type, payload = _read_packet(sock)
            return payload.decode('utf-8', errors='replace')
    except socket.timeout as exc:
        raise RconTimeoutError(f"RCON request to {host}:{port} timed out after {timeout}s") from exc
    except ConnectionRefusedError as exc:
        raise RconConnectionError(f"RCON connection to {host}:{port} refused") from exc
    except OSError as exc:
        raise RconConnectionError(f"RCON connection to {host}:{port} failed: {exc}") from exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest mcmanager/console/tests/test_rcon.py -v`
Expected: `4 passed`

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `python -m pytest -v`
Expected: all prior (Phase 1) tests still pass, plus the 4 new ones.

- [ ] **Step 6: Commit**

```bash
git add mcmanager/console/services/rcon.py mcmanager/console/tests/fixtures/ mcmanager/console/tests/test_rcon.py
git commit -m "feat: add self-implemented RCON protocol client"
```

---

### Task 2: RCON credentials on Server model + migration + provisioning

**Files:**
- Modify: `mcmanager/console/models.py:31-45` (the `Server` class)
- Modify: `mcmanager/console/services/provisioning.py` (full file)
- Modify: `mcmanager/console/tests/test_provisioning.py` (add one test)
- Create: `mcmanager/console/migrations/0006_*.py` (name depends on `makemigrations` output)

**Interfaces:**
- Consumes: `settings.SERVERS_DIR`/`settings.JAR_DIR`/`settings.CONFIGS_DIR` (unchanged from Phase 1).
- Produces: `Server.rcon_port` (int), `Server.rcon_password` (str, 24 chars); `provisioning.generate_rcon_credentials(server) -> (int, str)`; `provisioning.rewrite_properties(properties_path: Path, updates: dict) -> None`. Task 6's migration and Task 3/4's `process.py` both read `server.rcon_port`/`server.rcon_password` directly off the model — no other task touches `generate_rcon_credentials`/`rewrite_properties` directly except this task's own migration.

**Context:** `rcon_port = server.port + 10000` (fixed offset — a 25565 game port gets RCON on 35565). Credentials are placeholder (`0`/`''`) until `provisioning.create_server_files()` runs — same pattern the existing `jar` field already uses (blank until first provisioned). This keeps every existing Phase 1 test fixture that does a bare `Server.objects.create(...)` working unchanged.

- [ ] **Step 1: Write the failing test**

Add to `mcmanager/console/tests/test_provisioning.py` (append after the existing `test_create_server_files_provisions_new_server` test — keep all existing tests in the file unchanged):

```python
@pytest.mark.django_db
def test_create_server_files_generates_rcon_credentials(server_type, jar_dir, configs_dir, servers_dir):
    server = Server.objects.create(
        name="Test", jar_template="paper.jar", port=25566, type=server_type
    )

    provisioning.create_server_files(server)

    server.refresh_from_db()
    server_dir = servers_dir / f"server_{server.id}"
    properties_text = (server_dir / "server.properties").read_text(encoding="utf-8")
    assert server.rcon_port == server.port + 10000
    assert len(server.rcon_password) == 24
    assert f"rcon.port={server.rcon_port}" in properties_text
    assert f"rcon.password={server.rcon_password}" in properties_text
    assert "enable-rcon=true" in properties_text
    assert "rcon.bind=127.0.0.1" in properties_text
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest mcmanager/console/tests/test_provisioning.py::test_create_server_files_generates_rcon_credentials -v`
Expected: FAIL — `django.core.exceptions.FieldError` or `AttributeError: 'Server' object has no attribute 'rcon_port'` (field doesn't exist yet).

- [ ] **Step 3: Add the model fields**

In `mcmanager/console/models.py`, replace the `Server` class (lines 31-45) with:

```python
class Server(models.Model):
    name = models.CharField(max_length=100)
    jar_template = models.CharField(max_length=100)
    jar = models.CharField(max_length=100, blank=True, null=True)
    port = models.IntegerField(default=25565)
    memory_limit = models.IntegerField("Memory Limit (MB)", default=1024)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    type = models.ForeignKey(
        Type, on_delete=models.CASCADE, related_name='servers')
    status = models.BooleanField(default=False)
    server_properties = models.TextField(blank=True, null=True)
    rcon_port = models.IntegerField(editable=False, default=0)
    rcon_password = models.CharField(max_length=32, editable=False, default='', blank=True)

    def __str__(self):
        return self.name
```

(`status` stays for now — Task 6 removes it once nothing depends on it anymore.)

- [ ] **Step 4: Rewrite provisioning.py to generate and write RCON credentials**

Replace the full contents of `mcmanager/console/services/provisioning.py` with:

```python
import shutil
import string

from django.conf import settings
from django.utils.crypto import get_random_string

RCON_PORT_OFFSET = 10000
_RCON_PASSWORD_ALPHABET = string.ascii_letters + string.digits


def generate_rcon_credentials(server):
    """Returns (rcon_port, rcon_password) for `server`. Does not persist anything."""
    rcon_port = server.port + RCON_PORT_OFFSET
    rcon_password = get_random_string(24, _RCON_PASSWORD_ALPHABET)
    return rcon_port, rcon_password


def rewrite_properties(properties_path, updates):
    """Rewrites existing `key=value` lines in place; appends keys not already present.
    Used both for first-time provisioning and the Task 6 RCON-credential backfill migration."""
    lines = properties_path.read_text(encoding='utf-8', errors='ignore').splitlines(keepends=True)
    remaining = dict(updates)
    new_lines = []
    for line in lines:
        matched_key = next((k for k in remaining if line.startswith(f'{k}=')), None)
        if matched_key is not None:
            new_lines.append(f'{matched_key}={remaining.pop(matched_key)}\n')
        else:
            new_lines.append(line)
    for key, value in remaining.items():
        new_lines.append(f'{key}={value}\n')
    properties_path.write_text(''.join(new_lines), encoding='utf-8')


def create_server_files(server):
    server_path = settings.SERVERS_DIR / f'server_{server.id}'
    properties_path = server_path / 'server.properties'

    server.jar = f'{server.id}_{server.jar_template}'
    server.rcon_port, server.rcon_password = generate_rcon_credentials(server)
    server_path.mkdir(parents=True, exist_ok=True)
    shutil.copy(settings.JAR_DIR / server.jar_template, server_path / server.jar)
    shutil.copy(settings.CONFIGS_DIR / 'server.properties', properties_path)

    if server.type.dependencies:
        for dependency in server.type.dependencies:
            dep_source = settings.JAR_DIR / dependency
            dep_dest = server_path / dependency
            if dep_source.is_dir():
                shutil.copytree(dep_source, dep_dest)
            else:
                shutil.copy(dep_source, dep_dest)

    (server_path / 'eula.txt').write_text('eula=true', encoding='utf-8')

    rewrite_properties(properties_path, {
        'server-port': server.port,
        'enable-rcon': 'true',
        'rcon.port': server.rcon_port,
        'rcon.password': server.rcon_password,
        'rcon.bind': '127.0.0.1',
    })

    server.server_properties = properties_path.read_text(encoding='utf-8', errors='ignore')
    server.save(update_fields=['jar', 'rcon_port', 'rcon_password', 'server_properties'])


def sync_server_properties_file(server):
    server_path = settings.SERVERS_DIR / f'server_{server.id}'
    properties_path = server_path / 'server.properties'
    properties_path.write_text(server.server_properties or '', encoding='utf-8')


def delete_server_files(server):
    server_path = settings.SERVERS_DIR / f'server_{server.id}'
    if server_path.exists():
        shutil.rmtree(server_path)
```

- [ ] **Step 5: Generate the migration**

Run: `python manage.py makemigrations console`
Expected output includes a new migration adding `rcon_port` and `rcon_password` to `Server` (e.g. `0006_server_rcon_password_server_rcon_port.py` — use whatever Django actually generates for the rest of this task).

- [ ] **Step 6: Add the backfill for existing servers**

Open the migration file `makemigrations` just generated and add a `RunPython` operation that backfills existing `Server` rows. The migration checks server liveness using the **old** Phase 1 `/tmp` PID-file convention directly (not `services.process`, which doesn't exist as the new state-file mechanism until Task 3 — and won't be what's actually on disk for a still-running server at the moment this migration runs on an upgrading install, since the old convention is what Phase 1's code produced).

Edit the generated migration file so it reads (keep the auto-generated `AddField` operations exactly as `makemigrations` wrote them; add the two new functions and the `RunPython` operation):

```python
import os

import psutil
from django.conf import settings
from django.db import migrations, models

from mcmanager.console.services.provisioning import generate_rcon_credentials, rewrite_properties


def _is_server_running_pre_phase2(server_id):
    pid_file = f'/tmp/minecraft_server_{server_id}.pid'
    if not os.path.exists(pid_file):
        return False
    try:
        with open(pid_file, 'r') as f:
            pid = int(f.read().strip())
        return psutil.pid_exists(pid)
    except (ValueError, OSError):
        return False


def backfill_rcon_credentials(apps, schema_editor):
    Server = apps.get_model('console', 'Server')
    for server in Server.objects.all():
        if _is_server_running_pre_phase2(server.id):
            print(f"[!] Skipping RCON backfill for server {server.id} ({server.name}): "
                  f"it's currently running. Re-run this migration after stopping it.")
            continue
        rcon_port, rcon_password = generate_rcon_credentials(server)
        properties_path = settings.SERVERS_DIR / f'server_{server.id}' / 'server.properties'
        if properties_path.exists():
            rewrite_properties(properties_path, {
                'enable-rcon': 'true',
                'rcon.port': rcon_port,
                'rcon.password': rcon_password,
                'rcon.bind': '127.0.0.1',
            })
            server.server_properties = properties_path.read_text(encoding='utf-8', errors='ignore')
        server.rcon_port = rcon_port
        server.rcon_password = rcon_password
        server.save(update_fields=['rcon_port', 'rcon_password', 'server_properties'])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('console', '0005_alter_server_jar_template_and_more'),
    ]

    operations = [
        # ... keep the auto-generated AddField operations here exactly as makemigrations wrote them ...
        migrations.RunPython(backfill_rcon_credentials, noop_reverse),
    ]
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `python -m pytest mcmanager/console/tests/test_provisioning.py -v`
Expected: all tests pass, including the new `test_create_server_files_generates_rcon_credentials`.

- [ ] **Step 8: Run the full suite to check for regressions**

Run: `python -m pytest -v`
Expected: all tests pass (Phase 1 tests unaffected since `rcon_port`/`rcon_password` have defaults).

- [ ] **Step 9: Commit**

```bash
git add mcmanager/console/models.py mcmanager/console/services/provisioning.py mcmanager/console/tests/test_provisioning.py mcmanager/console/migrations/0006_*.py
git commit -m "feat: add RCON credentials to Server model and provisioning"
```

---

### Task 3: Process lifecycle — start, is_running, force_stop, get_stats

**Files:**
- Modify: `mcmanager/settings.py:32-39`
- Modify: `mcmanager/cli.py:44-48`
- Create: `mcmanager/console/services/process.py`
- Create: `mcmanager/console/tests/fixtures/fake_server.py`
- Create: `mcmanager/console/tests/fixtures/fake_java_binary.py`
- Create: `mcmanager/console/tests/test_process.py`

**Interfaces:**
- Consumes: `Server.id`/`.jar`/`.memory_limit` (existing fields); `settings.SERVERS_DIR`, `settings.JAVA_BIN_PATH` (existing); `settings.RUN_DIR` (new, this task).
- Produces: `process.AlreadyRunningError`, `process.ProcessNotRunningError`, `process.JavaNotFoundError`, `process.StopTimeoutError` (exception classes — `StopTimeoutError` isn't raised until Task 4, but is declared here alongside the others as the module's full public exception vocabulary); `process.start(server) -> None`, `process.is_running(server) -> bool`, `process.force_stop(server) -> None`, `process.get_stats(server) -> dict`, `process._read_state(server) -> dict | None`, `process._write_state(server, pid) -> None`, `process._clear_state(server) -> None`. Task 4 adds `stop()`/`send_command()` to this same file. Task 5's `views.py` calls the public functions.

**Context:** `settings.RUN_DIR` (`~/.mcmanager/run/`) replaces the Phase 1 `/tmp/minecraft_server_<id>.pid`/`.pty` convention with one JSON file per server: `{"pid": ..., "started_at": ..., "jar": ...}`. `is_running()` checks both PID existence **and** that the process's cmdline contains the expected jar filename, guarding against a recycled PID.

- [ ] **Step 1: Add RUN_DIR to settings and cli.py**

In `mcmanager/settings.py`, replace lines 32-39:

```python
USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
(USER_DATA_DIR / "servers").mkdir(exist_ok=True)
(USER_DATA_DIR / "jar").mkdir(exist_ok=True)
(USER_DATA_DIR / "configs").mkdir(exist_ok=True)

JAR_DIR = USER_DATA_DIR / 'jar'
SERVERS_DIR = USER_DATA_DIR / 'servers'
CONFIGS_DIR = USER_DATA_DIR / 'configs'
```

with:

```python
USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
(USER_DATA_DIR / "servers").mkdir(exist_ok=True)
(USER_DATA_DIR / "jar").mkdir(exist_ok=True)
(USER_DATA_DIR / "configs").mkdir(exist_ok=True)
(USER_DATA_DIR / "run").mkdir(exist_ok=True)

JAR_DIR = USER_DATA_DIR / 'jar'
SERVERS_DIR = USER_DATA_DIR / 'servers'
CONFIGS_DIR = USER_DATA_DIR / 'configs'
RUN_DIR = USER_DATA_DIR / 'run'
```

In `mcmanager/cli.py`, replace lines 44-48:

```python
    data_dir = get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "servers").mkdir(exist_ok=True)
    (data_dir / "jar").mkdir(exist_ok=True)
    (data_dir / "configs").mkdir(exist_ok=True)
```

with:

```python
    data_dir = get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "servers").mkdir(exist_ok=True)
    (data_dir / "jar").mkdir(exist_ok=True)
    (data_dir / "configs").mkdir(exist_ok=True)
    (data_dir / "run").mkdir(exist_ok=True)
```

- [ ] **Step 2: Write the test fixtures**

Create `mcmanager/console/tests/fixtures/fake_server.py` (this is a standalone script — it will be *executed* as a subprocess by tests, not imported):

```python
"""Stand-in for `java -jar server.jar nogui`, used only by mcmanager's own
test suite. If a server.properties file with enable-rcon=true exists in the
current working directory, it also serves a minimal RCON endpoint on
rcon.port/rcon.password from that file, mirroring how process.py's
stop()/send_command() interact with a real Minecraft server."""
import os
import signal
import socket
import struct
import threading
import time


def _read_server_properties():
    properties = {}
    if os.path.exists('server.properties'):
        with open('server.properties', 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, _, value = line.partition('=')
                properties[key] = value
    return properties


def _recv_exact(sock, n):
    chunks = []
    remaining = n
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            return b''.join(chunks)
        chunks.append(chunk)
        remaining -= len(chunk)
    return b''.join(chunks)


def _read_packet(sock):
    raw_len = _recv_exact(sock, 4)
    if len(raw_len) < 4:
        return None
    length = struct.unpack('<i', raw_len)[0]
    body = _recv_exact(sock, length)
    request_id, packet_type = struct.unpack('<ii', body[:8])
    payload = body[8:-2]
    return request_id, packet_type, payload


def _send_packet(sock, request_id, packet_type, payload):
    body = struct.pack('<ii', request_id, packet_type) + payload + b'\x00\x00'
    sock.sendall(struct.pack('<i', len(body)) + body)


def _serve_rcon(port, password, stop_event):
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind(('127.0.0.1', port))
    server_sock.listen(1)
    server_sock.settimeout(1.0)
    while not stop_event.is_set():
        try:
            conn, _addr = server_sock.accept()
        except socket.timeout:
            continue
        with conn:
            packet = _read_packet(conn)
            if packet is None:
                continue
            request_id, _packet_type, payload = packet
            if payload.decode('utf-8', errors='replace') != password:
                _send_packet(conn, -1, 2, b'')
                continue
            _send_packet(conn, request_id, 2, b'')
            while True:
                packet = _read_packet(conn)
                if packet is None:
                    break
                request_id, _packet_type, payload = packet
                command = payload.decode('utf-8', errors='replace')
                if command == 'stop':
                    _send_packet(conn, request_id, 0, b'Stopping the server')
                    stop_event.set()
                    return
                _send_packet(conn, request_id, 0, f'Unknown command: {command}'.encode('utf-8'))


def main():
    print(f'fake_server started, pid={os.getpid()}', flush=True)
    properties = _read_server_properties()
    stop_event = threading.Event()

    if properties.get('enable-rcon') == 'true':
        rcon_port = int(properties['rcon.port'])
        rcon_password = properties['rcon.password']
        rcon_thread = threading.Thread(
            target=_serve_rcon, args=(rcon_port, rcon_password, stop_event), daemon=True
        )
        rcon_thread.start()

    if hasattr(signal, 'SIGTERM'):
        try:
            signal.signal(signal.SIGTERM, lambda *_a: stop_event.set())
        except (ValueError, OSError):
            pass

    while not stop_event.is_set():
        time.sleep(0.1)
    print('fake_server stopping', flush=True)


if __name__ == '__main__':
    main()
```

Create `mcmanager/console/tests/fixtures/fake_java_binary.py`:

```python
"""Builds a small OS-appropriate executable that stands in for the `java`
binary in tests: it tolerates the -Xms/-Xmx/-jar/nogui argument shape
process.start() always passes (Python's own CLI parser rejects `-jar`
outright, so we can't point JAVA_BIN_PATH at sys.executable directly) and
launches fake_server.py underneath."""
import os
import stat
import sys
from pathlib import Path

_FAKE_SERVER_PATH = Path(__file__).parent / "fake_server.py"


def create_fake_java_binary(tmp_path: Path) -> Path:
    if os.name == "posix":
        wrapper = tmp_path / "fake_java"
        wrapper.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{_FAKE_SERVER_PATH}" "$@"\n',
            encoding="utf-8",
        )
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    else:
        wrapper = tmp_path / "fake_java.bat"
        wrapper.write_text(
            f'@echo off\r\n"{sys.executable}" "{_FAKE_SERVER_PATH}" %*\r\n',
            encoding="utf-8",
        )
    return wrapper
```

- [ ] **Step 3: Write the failing tests**

Create `mcmanager/console/tests/test_process.py`:

```python
import os
import time
from pathlib import Path

import psutil
import pytest

from mcmanager.console.models import Server, Type
from mcmanager.console.services import process, provisioning
from mcmanager.console.tests.fixtures.fake_java_binary import create_fake_java_binary


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
    s = Server.objects.create(name="Test", jar_template="paper.jar", port=25566, type=server_type)
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
def test_get_stats_raises_when_not_running(server):
    with pytest.raises(process.ProcessNotRunningError):
        process.get_stats(server)
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `python -m pytest mcmanager/console/tests/test_process.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcmanager.console.services.process'`

- [ ] **Step 5: Implement process.py**

Create `mcmanager/console/services/process.py`:

```python
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
                psutil.Process(pid).kill()
            except psutil.NoSuchProcess:
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
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest mcmanager/console/tests/test_process.py -v`
Expected: `7 passed`

- [ ] **Step 7: Run the full suite to check for regressions**

Run: `python -m pytest -v`
Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add mcmanager/settings.py mcmanager/cli.py mcmanager/console/services/process.py mcmanager/console/tests/fixtures/fake_server.py mcmanager/console/tests/fixtures/fake_java_binary.py mcmanager/console/tests/test_process.py
git commit -m "feat: add cross-platform process start/stop/stats via subprocess+psutil"
```

---

### Task 4: RCON-based graceful stop and command execution

**Files:**
- Modify: `mcmanager/console/services/process.py` (append `stop`/`send_command`)
- Modify: `mcmanager/console/tests/test_process.py` (append tests)

**Interfaces:**
- Consumes: `rcon.execute`, `rcon.DEFAULT_TIMEOUT`, `rcon.RconError` (Task 1); `Server.rcon_port`/`.rcon_password` (Task 2); `is_running`/`_read_state`/`_clear_state` (Task 3, same file).
- Produces: `process.stop(server) -> None`, `process.send_command(server, command: str) -> str`. Task 5's `views.py` calls both.

- [ ] **Step 1: Write the failing tests**

Append to `mcmanager/console/tests/test_process.py`:

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest mcmanager/console/tests/test_process.py -k "stop_sends or stop_raises or send_command" -v`
Expected: FAIL — `AttributeError: module 'mcmanager.console.services.process' has no attribute 'stop'`

- [ ] **Step 3: Implement stop() and send_command()**

Append to `mcmanager/console/services/process.py` (add the import at the top alongside the existing ones, then add the two functions at the end of the file):

Add to the imports at the top:
```python
from . import rcon
```

Add after `get_stats`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest mcmanager/console/tests/test_process.py -v`
Expected: `11 passed`

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `python -m pytest -v`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add mcmanager/console/services/process.py mcmanager/console/tests/test_process.py
git commit -m "feat: add RCON-based graceful stop and command execution to process manager"
```

---

### Task 5: Wire views.py to the process manager

**Files:**
- Modify: `mcmanager/console/views.py` (full file)
- Create: `mcmanager/console/tests/test_views_start_stop_e2e.py`
- Delete: `mcmanager/console/tests/test_force_stop.py` (superseded by `test_process.py`, Tasks 3-4)

**Interfaces:**
- Consumes: `process.start/is_running/force_stop/stop/send_command/get_stats` and all `process.*Error` exceptions (Tasks 3-4); `rcon.RconError` (Task 1).
- Produces: no new interfaces — URL names and JSON response shapes are unchanged from Phase 1. `is_server_running` (the Phase 1 helper) is deleted; nothing outside this file referenced it except `mcmanager/console/apps.py`, which Task 6 rewrites anyway.

**Context:** Every mutating/read view becomes a thin wrapper: call into `process.py`, translate its exceptions into the same JSON shape Phase 1 already used. `test_view_permissions.py` (Phase 1) needs no changes — it only exercises auth/POST/CSRF enforcement, which decorators intercept before any view body (and therefore any `process.py` call) executes.

- [ ] **Step 1: Write the failing end-to-end test**

Create `mcmanager/console/tests/test_views_start_stop_e2e.py`:

```python
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
        assert resp.status_code == 200
        assert resp.json()["status"] == "error"
    finally:
        process.force_stop(server)


@pytest.mark.django_db
def test_stop_server_when_not_running_returns_error(staff_client, provisioned_server):
    resp = staff_client.post(f"/console/stop_server/{provisioned_server.id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "error"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest mcmanager/console/tests/test_views_start_stop_e2e.py -v`
Expected: FAIL — the current `views.py` still uses the Phase 1 `/tmp` PID-file/`pty.fork` implementation, which will error (`pty` is `None` on Windows) or simply not match this test's expectations.

- [ ] **Step 3: Delete the obsolete Phase 1 test**

```bash
rm "mcmanager/console/tests/test_force_stop.py"
```

(Its coverage — killing a tracked process via PID lookup, not shell pattern-matching — is now provided by `test_process.py::test_force_stop_kills_process_and_clears_state`, Task 3.)

- [ ] **Step 4: Rewrite views.py**

Replace the full contents of `mcmanager/console/views.py` with:

```python
from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpRequest, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .models import Server
from .services import process, rcon

LOG_FILE = 'logs/latest.log'


@staff_member_required
def index(request, id):
    server = Server.objects.get(id=id)
    server_running = process.is_running(server)
    return render(request, 'console/index.html', {'server_running': server_running, 'server': server})


@staff_member_required
@require_POST
def start_server(request, id):
    server = Server.objects.get(id=id)
    try:
        process.start(server)
        return JsonResponse({'status': 'success', 'message': 'Server started'})
    except process.AlreadyRunningError:
        return JsonResponse({'status': 'error', 'message': 'Server is already running'})
    except process.JavaNotFoundError as e:
        return JsonResponse({'status': 'error', 'message': str(e)})


@staff_member_required
@require_POST
def force_stop_server(request, id):
    server = Server.objects.get(id=id)
    process.force_stop(server)
    return JsonResponse({'status': 'success', 'message': 'Server stopped'})


@staff_member_required
@require_POST
def stop_server(request, id):
    server = Server.objects.get(id=id)
    try:
        process.stop(server)
        return JsonResponse({'status': 'success', 'message': 'Server stopped'})
    except process.ProcessNotRunningError:
        return JsonResponse({'status': 'error', 'message': 'Server is not running'})
    except process.StopTimeoutError as e:
        return JsonResponse({'status': 'error', 'message': str(e)})
    except rcon.RconError as e:
        return JsonResponse({'status': 'error', 'message': str(e)})


@staff_member_required
def view_logs(request, id):
    server = Server.objects.get(id=id)
    log_path = settings.SERVERS_DIR / f'server_{server.id}' / LOG_FILE
    if log_path.exists():
        logs = log_path.read_text(encoding='utf8', errors='ignore')
        return JsonResponse({'status': 'success', 'logs': logs})
    return JsonResponse({'status': 'error', 'message': 'Log file not found'})


@staff_member_required
@require_POST
def send_command(request, id):
    server = Server.objects.get(id=id)
    command = request.POST.get('command')
    try:
        response = process.send_command(server, command)
        return JsonResponse({'status': 'success', 'message': response or 'Command sent'})
    except process.ProcessNotRunningError:
        return JsonResponse({'status': 'error', 'message': 'Server is not running'})
    except rcon.RconError as e:
        return JsonResponse({'status': 'error', 'message': str(e)})


@staff_member_required
def get_server_stats(request, id):
    server = Server.objects.get(id=id)
    try:
        stats = process.get_stats(server)
        return JsonResponse({'status': 'success', **stats})
    except process.ProcessNotRunningError:
        return JsonResponse({'status': 'error', 'message': 'Server is not running'})


@staff_member_required
def home(request: HttpRequest):
    ctx = {"servers": Server.objects.all()}
    if request.method == 'POST':
        server_id = request.POST.get('server_id')
        return redirect('index', id=server_id)
    return render(request, 'index.html', ctx)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest mcmanager/console/tests/test_views_start_stop_e2e.py -v`
Expected: `5 passed`

- [ ] **Step 6: Run the full suite to check for regressions**

Run: `python -m pytest -v`
Expected: all tests pass, including Phase 1's `test_view_permissions.py` (unaffected — auth/POST/CSRF decorators intercept before any `process.py` call).

- [ ] **Step 7: Commit**

```bash
git add mcmanager/console/views.py mcmanager/console/tests/test_views_start_stop_e2e.py
git rm mcmanager/console/tests/test_force_stop.py
git commit -m "refactor: wire views to the new process manager, replacing pty/PID-file handling"
```

---

### Task 6: Remove Server.status in favor of live process state

**Files:**
- Modify: `mcmanager/console/models.py` (remove `status` field)
- Modify: `mcmanager/console/admin.py` (full file)
- Modify: `mcmanager/console/apps.py` (full file)
- Modify: `mcmanager/console/templates/console/index.html:20-27`
- Modify: `mcmanager/console/views.py` (`home` function)
- Modify: `mcmanager/console/templates/index.html`
- Create: `mcmanager/console/migrations/0007_*.py`

**Interfaces:**
- Consumes: `process.is_running` (Task 3).
- Produces: no new interfaces — this is the final cleanup task. Nothing later in this plan depends on it.

**Context:** This closes out the design's "Notable Side Effect": `ConsoleConfig.ready()` (`mcmanager/console/apps.py`) ran a `Server.objects.all()` query/save loop at Django app-registry population time purely to keep `status` in sync — a data-safety concern flagged during Phase 1's review. With `status` gone, that loop has no reason to exist.

- [ ] **Step 1: Remove the status field from the model**

In `mcmanager/console/models.py`, remove the line `status = models.BooleanField(default=False)` from the `Server` class.

- [ ] **Step 2: Generate the migration**

Run: `python manage.py makemigrations console`
Expected output includes a `RemoveField` migration for `status` (e.g. `0007_remove_server_status.py` — use whatever Django actually generates).

- [ ] **Step 3: Update admin.py**

Replace the full contents of `mcmanager/console/admin.py` with:

```python
from typing import Any

from django.contrib import admin

from .forms import ServerForm
from .models import Server, Type
from .services import process, provisioning


@admin.register(Server)
class ServerAdmin(admin.ModelAdmin):
    form = ServerForm
    list_display = ('name', 'port', 'type', 'is_running')
    exclude = ('jar', 'server_properties')
    search_fields = ('name', 'port', 'type')
    list_filter = ('type',)
    readonly_fields = ()

    def is_running(self, obj):
        return process.is_running(obj)
    is_running.boolean = True
    is_running.short_description = 'Running'

    def get_form(self, request, obj=..., change=..., **kwargs) -> Any:
        if obj is None:
            self.exclude = ('jar', 'server_properties')
            self.readonly_fields = ()
        else:
            self.exclude = None
            self.readonly_fields = ('jar',)
        return super().get_form(request, obj, change, **kwargs)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not obj.jar:
            provisioning.create_server_files(obj)
        else:
            provisioning.sync_server_properties_file(obj)

    def delete_model(self, request, obj):
        provisioning.delete_server_files(obj)
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            provisioning.delete_server_files(obj)
        super().delete_queryset(request, queryset)


@admin.register(Type)
class TypeAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)
```

- [ ] **Step 4: Remove the status-sync loop from apps.py**

Replace the full contents of `mcmanager/console/apps.py` with:

```python
from django.apps import AppConfig


class ConsoleConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'mcmanager.console'
```

- [ ] **Step 5: Update the server detail template**

In `mcmanager/console/templates/console/index.html`, replace lines 20-27:

```html
            <h1 class="text-3xl font-bold mb-4">
                Server: {{ server.name }}
                {% if server.status %}
                    (ON)
                {% else %}
                    (OFF)
                {% endif %}
            </h1>
```

with:

```html
            <h1 class="text-3xl font-bold mb-4">
                Server: {{ server.name }}
                {% if server_running %}
                    (ON)
                {% else %}
                    (OFF)
                {% endif %}
            </h1>
```

(`server_running` is already in this template's context, passed by the `index` view since Phase 1 — this template just wasn't using it for the header text.)

- [ ] **Step 6: Update home() to compute live status per server**

In `mcmanager/console/views.py`, replace the `home` function:

```python
@staff_member_required
def home(request: HttpRequest):
    ctx = {"servers": Server.objects.all()}
    if request.method == 'POST':
        server_id = request.POST.get('server_id')
        return redirect('index', id=server_id)
    return render(request, 'index.html', ctx)
```

with:

```python
@staff_member_required
def home(request: HttpRequest):
    if request.method == 'POST':
        server_id = request.POST.get('server_id')
        return redirect('index', id=server_id)
    ctx = {"servers": [(s, process.is_running(s)) for s in Server.objects.all()]}
    return render(request, 'index.html', ctx)
```

- [ ] **Step 7: Update the home page template**

In `mcmanager/console/templates/index.html`, replace:

```html
                    {% for server in servers %}
                        <option value="{{ server.id }}">
                            {{ server.name }}
                            {% if server.status %}
                                (ON)
                            {% else %}
                                (OFF)
                            {% endif %}
                        </option>
                    {% endfor %}
```

with:

```html
                    {% for server, is_running in servers %}
                        <option value="{{ server.id }}">
                            {{ server.name }}
                            {% if is_running %}
                                (ON)
                            {% else %}
                                (OFF)
                            {% endif %}
                        </option>
                    {% endfor %}
```

- [ ] **Step 8: Run the full suite to check for regressions**

Run: `python -m pytest -v`
Expected: all tests pass. (No test directly exercised the `home` page's template rendering or admin's `list_display` in Phase 1/this plan, so this step's real verification is `python manage.py check` catching any remaining broken reference — see next step.)

- [ ] **Step 9: Verify manage.py check passes**

Run: `python manage.py check`
Expected: `System check identified no issues (0 silenced).` — this specifically catches any leftover `list_display`/`list_filter`/`readonly_fields` entry admin.py that still names a nonexistent `status` field, which Django's admin checks framework (E108/E116) flags at startup.

- [ ] **Step 10: Commit**

```bash
git add mcmanager/console/models.py mcmanager/console/admin.py mcmanager/console/apps.py mcmanager/console/views.py mcmanager/console/templates/console/index.html mcmanager/console/templates/index.html mcmanager/console/migrations/0007_*.py
git commit -m "refactor: remove Server.status field in favor of live process state"
```

---

## Phase 2 exit check

After Task 6's commit, re-run the full suite once more (`python -m pytest -v`) and confirm:
- No `/tmp`, `pty`, or `os.popen`/shell-based process handling remains anywhere in `mcmanager/console/`.
- `python manage.py runserver` and the admin's server create/edit/delete flow still work end to end.
- All process-lifecycle tests (`test_rcon.py`, `test_process.py`, `test_views_start_stop_e2e.py`) pass without any `@pytest.mark.skipif(os.name != "posix", ...)` markers — this phase is genuinely cross-platform, unlike Phase 1's process-adjacent tests.
- The follow-up flagged during Phase 1 (spawned task `task_7b3489a0`, `ConsoleConfig.ready()` touching real data during test runs) is resolved — dismiss that task if it's still open.

This closes out Phase 2 of `docs/design-melhorias.md` and `docs/superpowers/specs/2026-07-17-mcmanager-phase2-design.md`. Phase 3 (real-time console, jar downloads, backups, auto-restart) is a separate plan.
