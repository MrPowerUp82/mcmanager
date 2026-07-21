# Auto-Restart and Scheduling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A single background supervisor thread that restarts servers that crashed unexpectedly (respecting a retry limit) and triggers a daily backup at a per-server scheduled time.

**Architecture:** Five new fields on `Server` (`auto_restart_enabled`, `desired_running`, `consecutive_restart_failures`, `scheduled_backup_time`, `last_scheduled_backup_date`) plus a new `services/supervisor.py` module with a single daemon thread ticking every 30s, calling `_check_auto_restart`/`_check_scheduled_backup` for every `Server` row. `desired_running` is intent (set by the start/stop views), not a status cache. The thread is started explicitly by `mcmanager run`, never by `ConsoleConfig.ready()`.

**Tech Stack:** Django (existing), stdlib `threading`/`datetime` — no new runtime dependencies.

## Global Constraints

- No new runtime dependencies.
- The supervisor thread is started explicitly in `cli.py`'s `run` command, never in `ConsoleConfig.ready()` — it must not run during `migrate`/`shell`/`createsuperuser`/tests.
- `desired_running` is set to `True` by a successful `start_server`, and `False` by a successful `stop_server`/`force_stop_server` — it represents intent, not a live status cache (the `Server.status` field removed in an earlier plan was a cache and was removed for exactly that reason; this is different and must not regress back into a cache).
- Auto-restart only fires when `auto_restart_enabled` AND `desired_running` are both `True`.
- After `MAX_RESTART_ATTEMPTS = 3` consecutive failed restart attempts, `auto_restart_enabled` is set to `False` automatically and no further attempts are made until a human re-enables it.
- `process.AlreadyRunningError` from an attempted restart (a concurrent request already restarted it) counts as success (resets the failure counter), not a failure.
- Scheduled backup time comparisons use `datetime.now(timezone.utc)`, consistent with this project's `TIME_ZONE = 'UTC'` setting.
- A scheduled backup fires at most once per calendar day per server, tracked via `last_scheduled_backup_date`.
- No scheduled restart — only scheduled backup (YAGNI, per the approved spec).
- The supervisor tests call `_tick()` directly, never `_run_forever()` (an infinite loop) or `start()` in a way that leaves a real background thread running after the test.

---

## File Structure

```
mcmanager/console/
├── models.py                       # + 5 fields on Server
├── services/
│   └── supervisor.py                # Create: the tick loop
├── views.py                         # Modify: start/stop/force_stop set desired_running
├── admin.py                         # Modify: exclude internal supervisor fields
├── migrations/
│   └── 0010_*.py                    # Create: the 5 new fields
└── tests/
    ├── test_models.py                # Modify: + defaults test
    ├── test_supervisor.py            # Create: auto-restart + scheduled backup
    └── test_views_desired_running.py # Create: views set desired_running correctly
mcmanager/cli.py                     # Modify: start the supervisor thread in `run`
```

---

### Task 1: Server model fields and migration

**Files:**
- Modify: `mcmanager/console/models.py:36-48` (the `Server` class's field list)
- Modify: `mcmanager/console/tests/test_models.py` (append)
- Create: `mcmanager/console/migrations/0010_*.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `Server.auto_restart_enabled` (bool, default `False`), `Server.desired_running` (bool, default `False`), `Server.consecutive_restart_failures` (int, default `0`), `Server.scheduled_backup_time` (nullable `TimeField`), `Server.last_scheduled_backup_date` (nullable `DateField`). Tasks 2-4 read/write all five.

- [ ] **Step 1: Write the failing test**

Append to `mcmanager/console/tests/test_models.py`:

```python
@pytest.mark.django_db
def test_server_defaults_have_auto_restart_disabled_and_no_schedule(server_type):
    server = Server.objects.create(name="Test", jar_template="paper.jar", port=25566, type=server_type)

    assert server.auto_restart_enabled is False
    assert server.desired_running is False
    assert server.consecutive_restart_failures == 0
    assert server.scheduled_backup_time is None
    assert server.last_scheduled_backup_date is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest mcmanager/console/tests/test_models.py -v -k defaults_have_auto_restart`
Expected: FAIL — `AttributeError: 'Server' object has no attribute 'auto_restart_enabled'`

- [ ] **Step 3: Add the fields**

In `mcmanager/console/models.py`, change the `Server` class from:

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
    server_properties = models.TextField(blank=True, null=True)
    rcon_port = models.IntegerField(editable=False, default=0)
    rcon_password = models.CharField(max_length=32, editable=False, default='', blank=True)
```

to:

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
    server_properties = models.TextField(blank=True, null=True)
    rcon_port = models.IntegerField(editable=False, default=0)
    rcon_password = models.CharField(max_length=32, editable=False, default='', blank=True)
    auto_restart_enabled = models.BooleanField(default=False)
    desired_running = models.BooleanField(default=False, editable=False)
    consecutive_restart_failures = models.IntegerField(default=0, editable=False)
    scheduled_backup_time = models.TimeField(null=True, blank=True)
    last_scheduled_backup_date = models.DateField(null=True, blank=True, editable=False)
```

- [ ] **Step 4: Generate the migration**

Run: `python manage.py makemigrations console`
Expected output includes: `Migrations for 'console': mcmanager\console\migrations\0010_server_auto_restart_enabled_and_more.py` (if the generated filename differs, use the actual generated name for the rest of this task).

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest mcmanager/console/tests/test_models.py -v -k defaults_have_auto_restart`
Expected: `1 passed`

- [ ] **Step 6: Run the full suite to check for regressions**

Run: `python -m pytest -v`
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add mcmanager/console/models.py mcmanager/console/tests/test_models.py mcmanager/console/migrations/0010_*.py
git commit -m "feat: add auto-restart and scheduled-backup fields to Server"
```

---

### Task 2: Supervisor auto-restart logic

**Files:**
- Create: `mcmanager/console/services/supervisor.py`
- Test: `mcmanager/console/tests/test_supervisor.py`

**Interfaces:**
- Consumes: `Server` model (Task 1's new fields); `mcmanager.console.services.process.{is_running,start,AlreadyRunningError}` (unchanged, from an earlier plan).
- Produces: `mcmanager.console.services.supervisor.MAX_RESTART_ATTEMPTS` (int constant, `3`), `mcmanager.console.services.supervisor.start() -> threading.Thread`, `mcmanager.console.services.supervisor._tick()` (checks every `Server`; this task implements only the auto-restart half of what `_tick` does — Task 3 adds the scheduled-backup half to the same function). Task 4's `cli.py` change calls `supervisor.start()` by exact name.

- [ ] **Step 1: Write the failing tests**

Create `mcmanager/console/tests/test_supervisor.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest mcmanager/console/tests/test_supervisor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcmanager.console.services.supervisor'`

- [ ] **Step 3: Implement the supervisor's auto-restart check**

Create `mcmanager/console/services/supervisor.py`:

```python
"""Background supervisor: restarts servers that crashed unexpectedly and
triggers scheduled daily backups. Runs as a single daemon thread started
explicitly by `mcmanager run` (never during migrate/shell/tests)."""
import threading

from ..models import Server
from . import process

TICK_SECONDS = 30
MAX_RESTART_ATTEMPTS = 3

_stop_event = threading.Event()


def start():
    thread = threading.Thread(target=_run_forever, daemon=True)
    thread.start()
    return thread


def _run_forever():
    while not _stop_event.is_set():
        _tick()
        _stop_event.wait(TICK_SECONDS)


def _tick():
    for server in Server.objects.all():
        _check_auto_restart(server)


def _check_auto_restart(server):
    if not server.auto_restart_enabled or not server.desired_running:
        return
    if process.is_running(server):
        if server.consecutive_restart_failures:
            server.consecutive_restart_failures = 0
            server.save(update_fields=['consecutive_restart_failures'])
        return

    if server.consecutive_restart_failures >= MAX_RESTART_ATTEMPTS:
        server.auto_restart_enabled = False
        server.save(update_fields=['auto_restart_enabled'])
        return

    try:
        process.start(server)
    except process.AlreadyRunningError:
        if server.consecutive_restart_failures:
            server.consecutive_restart_failures = 0
            server.save(update_fields=['consecutive_restart_failures'])
        return
    except Exception:
        pass
    server.consecutive_restart_failures += 1
    server.save(update_fields=['consecutive_restart_failures'])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest mcmanager/console/tests/test_supervisor.py -v`
Expected: `6 passed`

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `python -m pytest -v`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add mcmanager/console/services/supervisor.py mcmanager/console/tests/test_supervisor.py
git commit -m "feat: add supervisor thread with auto-restart and retry-limit logic"
```

---

### Task 3: Scheduled daily backup

**Files:**
- Modify: `mcmanager/console/services/supervisor.py` (extend `_tick`, add `_check_scheduled_backup`)
- Modify: `mcmanager/console/tests/test_supervisor.py` (append)

**Interfaces:**
- Consumes: `Server.scheduled_backup_time`/`last_scheduled_backup_date` (Task 1); `mcmanager.console.services.backups.start_backup` (unchanged, from an earlier plan).
- Produces: `mcmanager.console.services.supervisor._check_scheduled_backup(server)`, now also called from `_tick`. This is the last task touching `supervisor.py` in this plan.

- [ ] **Step 1: Write the failing tests**

Append to `mcmanager/console/tests/test_supervisor.py`:

```python
from datetime import datetime, time, timedelta, timezone


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest mcmanager/console/tests/test_supervisor.py -v -k scheduled_backup`
Expected: FAIL — `AttributeError: module 'mcmanager.console.services.supervisor' has no attribute 'backups'` (the module doesn't import `backups` yet, and `_check_scheduled_backup` doesn't exist).

- [ ] **Step 3: Implement the scheduled-backup check**

In `mcmanager/console/services/supervisor.py`, change the import line from:

```python
from . import process
```

to:

```python
from datetime import datetime, timezone

from . import backups, process
```

Then change `_tick` from:

```python
def _tick():
    for server in Server.objects.all():
        _check_auto_restart(server)
```

to:

```python
def _tick():
    for server in Server.objects.all():
        _check_auto_restart(server)
        _check_scheduled_backup(server)
```

Then append this new function at the end of the file:

```python


def _check_scheduled_backup(server):
    if server.scheduled_backup_time is None:
        return
    now = datetime.now(timezone.utc)
    if server.last_scheduled_backup_date == now.date():
        return
    if now.time() < server.scheduled_backup_time:
        return
    server.last_scheduled_backup_date = now.date()
    server.save(update_fields=['last_scheduled_backup_date'])
    backups.start_backup(server)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest mcmanager/console/tests/test_supervisor.py -v`
Expected: `10 passed`

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `python -m pytest -v`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add mcmanager/console/services/supervisor.py mcmanager/console/tests/test_supervisor.py
git commit -m "feat: add scheduled daily backup check to the supervisor tick"
```

---

### Task 4: Wire desired_running into views, start the supervisor in cli.py, expose fields in admin

**Files:**
- Modify: `mcmanager/console/views.py:21-51` (`start_server`, `force_stop_server`, `stop_server`)
- Modify: `mcmanager/cli.py:100-105` (the `run` command)
- Modify: `mcmanager/console/admin.py`
- Test: `mcmanager/console/tests/test_views_desired_running.py`

**Interfaces:**
- Consumes: `Server.desired_running`/`consecutive_restart_failures` (Task 1); `mcmanager.console.services.supervisor.start` (Task 2).
- Produces: no new interfaces — this is the last task in this plan.

- [ ] **Step 1: Write the failing tests**

Create `mcmanager/console/tests/test_views_desired_running.py`:

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest mcmanager/console/tests/test_views_desired_running.py -v`
Expected: FAIL — all 3 tests fail on `assert server.desired_running is True`/`is False` (the field stays at its default `False` since nothing in the views sets it yet).

- [ ] **Step 3: Wire desired_running into the views**

In `mcmanager/console/views.py`, change `start_server` from:

```python
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
```

to:

```python
@staff_member_required
@require_POST
def start_server(request, id):
    server = Server.objects.get(id=id)
    try:
        process.start(server)
        server.desired_running = True
        server.consecutive_restart_failures = 0
        server.save(update_fields=['desired_running', 'consecutive_restart_failures'])
        return JsonResponse({'status': 'success', 'message': 'Server started'})
    except process.AlreadyRunningError:
        return JsonResponse({'status': 'error', 'message': 'Server is already running'})
    except process.JavaNotFoundError as e:
        return JsonResponse({'status': 'error', 'message': str(e)})
```

Change `force_stop_server` from:

```python
@staff_member_required
@require_POST
def force_stop_server(request, id):
    server = Server.objects.get(id=id)
    process.force_stop(server)
    return JsonResponse({'status': 'success', 'message': 'Server stopped'})
```

to:

```python
@staff_member_required
@require_POST
def force_stop_server(request, id):
    server = Server.objects.get(id=id)
    process.force_stop(server)
    server.desired_running = False
    server.save(update_fields=['desired_running'])
    return JsonResponse({'status': 'success', 'message': 'Server stopped'})
```

Change `stop_server` from:

```python
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
```

to:

```python
@staff_member_required
@require_POST
def stop_server(request, id):
    server = Server.objects.get(id=id)
    try:
        process.stop(server)
        server.desired_running = False
        server.save(update_fields=['desired_running'])
        return JsonResponse({'status': 'success', 'message': 'Server stopped'})
    except process.ProcessNotRunningError:
        return JsonResponse({'status': 'error', 'message': 'Server is not running'})
    except process.StopTimeoutError as e:
        return JsonResponse({'status': 'error', 'message': str(e)})
    except rcon.RconError as e:
        return JsonResponse({'status': 'error', 'message': str(e)})
```

- [ ] **Step 4: Start the supervisor thread in `mcmanager run`**

In `mcmanager/cli.py`, change:

```python
    elif args.command == "run":
        bind_address = f"{args.host}:{args.port}"
        print(f"[*] Iniciando servidor mcmanager em http://{bind_address}/")
        print("[*] Pressione CTRL+C para encerrar.")
        # Em produção, rodamos sem autoreload
        call_command("runserver", "--noreload", "--nostatic", bind_address)
```

to:

```python
    elif args.command == "run":
        from mcmanager.console.services import supervisor
        supervisor.start()
        bind_address = f"{args.host}:{args.port}"
        print(f"[*] Iniciando servidor mcmanager em http://{bind_address}/")
        print("[*] Pressione CTRL+C para encerrar.")
        # Em produção, rodamos sem autoreload
        call_command("runserver", "--noreload", "--nostatic", bind_address)
```

- [ ] **Step 5: Expose the right fields in the admin**

In `mcmanager/console/admin.py`, the `get_form` method currently reads:

```python
    def get_form(self, request, obj=..., change=..., **kwargs) -> Any:
        if obj is None:
            self.exclude = ('jar', 'server_properties')
            self.readonly_fields = ()
        else:
            self.exclude = None
            self.readonly_fields = ('jar',)
        return super().get_form(request, obj, change, **kwargs)
```

Change it to:

```python
    def get_form(self, request, obj=..., change=..., **kwargs) -> Any:
        internal_fields = ('desired_running', 'consecutive_restart_failures', 'last_scheduled_backup_date')
        if obj is None:
            self.exclude = ('jar', 'server_properties') + internal_fields
            self.readonly_fields = ()
        else:
            self.exclude = internal_fields
            self.readonly_fields = ('jar',)
        return super().get_form(request, obj, change, **kwargs)
```

Also update the class-level `exclude` attribute (used before `get_form` runs) from:

```python
    exclude = ('jar', 'server_properties')
```

to:

```python
    exclude = ('jar', 'server_properties', 'desired_running', 'consecutive_restart_failures', 'last_scheduled_backup_date')
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest mcmanager/console/tests/test_views_desired_running.py -v`
Expected: `3 passed`

- [ ] **Step 7: Run the full suite to check for regressions**

Run: `python -m pytest -v`
Expected: all tests pass.

- [ ] **Step 8: Manually verify in a browser**

This step exercises the full loop over real wall-clock time, which the automated suite (correctly) tests via direct `_tick()` calls instead of waiting 30 real seconds. On a machine with Java available:

```bash
python manage.py createsuperuser
python manage.py runserver
```

In the admin, create a server, check "Auto restart enabled", save. Start it from the console page (confirm `desired_running` becomes `True` in the admin). Kill the underlying Java process directly from your OS's task manager/`kill` (not via the panel's own stop button) to simulate a crash, then run `mcmanager` with `supervisor.start()` wired in a real `mcmanager run` session and confirm the server comes back up within ~30 seconds. Set a `scheduled_backup_time` a couple minutes in the future and confirm a backup appears once that time passes.

- [ ] **Step 9: Commit**

```bash
git add mcmanager/console/views.py mcmanager/cli.py mcmanager/console/admin.py mcmanager/console/tests/test_views_desired_running.py
git commit -m "feat: wire desired_running into start/stop views and start the supervisor in mcmanager run"
```

---

## Self-review notes

- **Spec coverage:** Task 1 covers the model fields. Task 2 covers auto-restart (respecting `desired_running`/`auto_restart_enabled`, the 3-attempt limit, and the `AlreadyRunningError`-as-success case). Task 3 covers the scheduled daily backup (fires once past the time, once per day, does nothing when unset). Task 4 covers the integration points (views setting `desired_running`, `cli.py` starting the thread only in `run`, admin exposing the right fields) and includes a manual verification step for the parts that need real wall-clock time / a real crash, which the automated suite can't practically cover. Every Global Constraint has a concrete implementation and, where testable without waiting on real time, a test.
- **Placeholder scan:** none found — every step has complete, exact code.
- **Type consistency:** `supervisor.MAX_RESTART_ATTEMPTS`, `supervisor.start()`, `supervisor._tick()` are used with the same names across Tasks 2-4. `Server`'s five new field names (`auto_restart_enabled`, `desired_running`, `consecutive_restart_failures`, `scheduled_backup_time`, `last_scheduled_backup_date`) are used identically in Task 1's definition, Task 2/3's supervisor logic, and Task 4's views/admin changes.
