# mcmanager Phase 1 — Security & Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the authentication, CSRF, and command-injection gaps in the mcmanager Django console, and replace the fragile signal-based provisioning with a tested service layer, without changing any URL, template contract, or the admin-based server-creation workflow.

**Architecture:** Same Django app (`mcmanager.console`). Business logic that currently lives in model signals moves into plain, directly-testable functions under `mcmanager/console/services/`. Views gain `@staff_member_required` / `@require_POST` decorators. No new runtime dependencies — `psutil` (already a dependency) replaces shell-based process lookup (`pgrep`/`pkill`).

**Tech Stack:** Django 4.x, SQLite, pytest + pytest-django (new, dev-only), psutil (existing).

## Global Constraints

- Python `>=3.8`, Django `>=4.2.0,<5.2.0` — from `pyproject.toml`; do not introduce packages requiring newer versions.
- No new **runtime** dependencies. `pytest`/`pytest-django` are dev-only (`requirements-dev.txt` + `[project.optional-dependencies].dev`), never added to `[project].dependencies`.
- No Celery, Redis, Postgres, or Django Channels — out of scope per the approved design (`docs/design-melhorias.md`, section "Não-objetivos").
- Scale target: 1 machine, ~1–10 servers, SQLite. No multi-node or multi-tenant abstractions.
- This phase stays **Linux-behavior-only** (PID files under `/tmp`, `os.kill`, `pty.fork`). Cross-platform process handling is explicitly Phase 2 (`docs/design-melhorias.md`, section 4) — do not attempt it here.
- After every task, `python manage.py runserver` and the Django admin's server create/edit/delete flow must keep working end to end. No half-migrated state between commits.
- Existing URL names and JSON response shapes (`{status, message, ...}`) must not change — Phase 4 owns response-contract standardization.

---

## File Structure

```
mcmanager/console/
├── models.py                      # Modify (Task 2, Task 3): drop signals, drop frozen choices
├── forms.py                       # Create (Task 3): ServerForm with dynamic jar choices
├── admin.py                       # Modify (Task 2, Task 3): wire provisioning + form
├── views.py                       # Modify (Task 4, Task 5): psutil kill, auth, POST-only
├── services/
│   ├── __init__.py                # Create (Task 2)
│   └── provisioning.py            # Create (Task 2): create/sync/delete server files
├── migrations/
│   └── 0005_alter_server_jar_template.py   # Create (Task 3): auto-generated
├── templates/console/index.html   # Modify (Task 5): POST + CSRF header for start/stop/force-stop
└── tests/
    ├── __init__.py                # Create (Task 1)
    ├── test_smoke.py              # Create (Task 1)
    ├── test_provisioning.py       # Create (Task 2)
    ├── test_forms.py              # Create (Task 3)
    └── test_view_permissions.py   # Create (Task 5)
mcmanager/console/tests.py         # Delete (Task 1): old empty stub, replaced by tests/ package
conftest.py                        # Create (Task 1): repo root, isolates test data dir
requirements-dev.txt               # Create (Task 1)
pyproject.toml                     # Modify (Task 1): pytest config + dev extra
```

---

### Task 1: pytest-django test infrastructure

**Files:**
- Delete: `mcmanager/console/tests.py`
- Create: `mcmanager/console/tests/__init__.py`
- Create: `mcmanager/console/tests/test_smoke.py`
- Create: `conftest.py` (repo root)
- Create: `requirements-dev.txt`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: a working `pytest` command that runs against an isolated temp data directory. Later tasks add files under `mcmanager/console/tests/` and rely on `conftest.py` having already redirected `MCMANAGER_DATA_DIR` before Django settings load.

- [ ] **Step 1: Remove the old empty test stub**

The current `mcmanager/console/tests.py` is an empty `TestCase` stub. It must be removed because Python cannot import both `console/tests.py` and a `console/tests/` package.

```bash
rm "mcmanager/console/tests.py"
```

- [ ] **Step 2: Write the smoke test**

Create `mcmanager/console/tests/__init__.py` (empty file).

Create `mcmanager/console/tests/test_smoke.py`:

```python
import pytest
from django.conf import settings

from mcmanager.console.models import Type


@pytest.mark.django_db
def test_can_create_type():
    server_type = Type.objects.create(name="Vanilla")
    assert server_type.pk is not None
    assert str(server_type) == "Vanilla"


def test_user_data_dir_is_an_isolated_temp_dir():
    assert "mcmanager-tests-" in str(settings.USER_DATA_DIR)
```

- [ ] **Step 3: Run it to confirm it fails**

Run: `python -m pytest mcmanager/console/tests/test_smoke.py -v`
Expected: FAIL — `pytest` is either not installed, or collection fails because Django settings aren't configured for pytest yet (no `DJANGO_SETTINGS_MODULE` wiring).

- [ ] **Step 4: Add the root conftest.py that isolates the test data directory**

`mcmanager/settings.py` reads `MCMANAGER_DATA_DIR` at import time to decide where `db.sqlite3`, `servers/`, `jar/`, and `configs/` live. Tests must redirect this to a throwaway directory *before* Django settings are imported, otherwise test runs would read/write the real project data dir.

Create `conftest.py` at the repo root:

```python
import os
import tempfile

os.environ.setdefault("MCMANAGER_DATA_DIR", tempfile.mkdtemp(prefix="mcmanager-tests-"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mcmanager.settings")
```

- [ ] **Step 5: Add pytest-django configuration and dev dependencies**

Modify `pyproject.toml` — add after the existing `[project]` table (after line 23's closing `]` and before `[project.scripts]`):

```toml
[project.optional-dependencies]
dev = ["pytest>=7.4.0", "pytest-django>=4.7.0"]
```

Add a new section at the end of the file:

```toml
[tool.pytest.ini_options]
DJANGO_SETTINGS_MODULE = "mcmanager.settings"
```

Create `requirements-dev.txt`:

```
-r requirements.txt
pytest>=7.4.0
pytest-django>=4.7.0
```

- [ ] **Step 6: Install dev dependencies**

Run: `pip install -r requirements-dev.txt`
Expected: `pytest` and `pytest-django` install successfully alongside the existing runtime deps.

- [ ] **Step 7: Run the smoke test again to confirm it passes**

Run: `python -m pytest mcmanager/console/tests/test_smoke.py -v`
Expected: `2 passed`

- [ ] **Step 8: Commit**

```bash
git add mcmanager/console/tests/ conftest.py requirements-dev.txt pyproject.toml
git rm mcmanager/console/tests.py
git commit -m "test: configure pytest-django with isolated test data directory"
```

---

### Task 2: Extract provisioning service from model signals

**Files:**
- Create: `mcmanager/console/services/__init__.py`
- Create: `mcmanager/console/services/provisioning.py`
- Create: `mcmanager/console/tests/test_provisioning.py`
- Modify: `mcmanager/console/models.py`
- Modify: `mcmanager/console/admin.py`

**Interfaces:**
- Consumes: `Server`, `Type` models (unchanged fields); `settings.JAR_DIR`, `settings.CONFIGS_DIR`, `settings.SERVERS_DIR` (all `pathlib.Path`, defined unconditionally in `mcmanager/settings.py:37-39`).
- Produces: `mcmanager.console.services.provisioning.create_server_files(server: Server) -> None`, `sync_server_properties_file(server: Server) -> None`, `delete_server_files(server: Server) -> None`. `ServerAdmin.save_model/delete_model/delete_queryset` overrides that call them. Task 3 and Task 5 build on top of this `ServerAdmin` without touching these three methods.

- [ ] **Step 1: Write the failing tests**

Create `mcmanager/console/tests/test_provisioning.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest mcmanager/console/tests/test_provisioning.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcmanager.console.services'` (the import at the top of the test file fails during collection).

- [ ] **Step 3: Create the provisioning service**

Create `mcmanager/console/services/__init__.py` (empty file).

Create `mcmanager/console/services/provisioning.py`:

```python
import shutil

from django.conf import settings


def create_server_files(server):
    server_path = settings.SERVERS_DIR / f'server_{server.id}'
    properties_path = server_path / 'server.properties'

    server.jar = f'{server.id}_{server.jar_template}'
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

    lines = properties_path.read_text(encoding='utf-8', errors='ignore').splitlines(keepends=True)
    new_lines = [
        f'server-port={server.port}\n' if line.startswith('server-port=') else line
        for line in lines
    ]
    properties_path.write_text(''.join(new_lines), encoding='utf-8')

    server.server_properties = properties_path.read_text(encoding='utf-8', errors='ignore')
    server.save(update_fields=['jar', 'server_properties'])


def sync_server_properties_file(server):
    server_path = settings.SERVERS_DIR / f'server_{server.id}'
    properties_path = server_path / 'server.properties'
    properties_path.write_text(server.server_properties or '', encoding='utf-8')


def delete_server_files(server):
    server_path = settings.SERVERS_DIR / f'server_{server.id}'
    if server_path.exists():
        shutil.rmtree(server_path)
```

- [ ] **Step 4: Remove the signals from models.py**

Replace the full contents of `mcmanager/console/models.py` with:

```python
import os

from django.conf import settings
from django.db import models


def get_jar_files():
    jar_dir = getattr(settings, 'JAR_DIR', os.path.join(settings.BASE_DIR, 'jar'))
    if not os.path.exists(jar_dir):
        os.makedirs(jar_dir)
    return [(x, x) for x in os.listdir(jar_dir) if x.endswith('.jar')]


def get_default_server_prop():
    configs_dir = getattr(settings, 'CONFIGS_DIR', os.path.join(settings.BASE_DIR, 'configs'))
    with open(os.path.join(configs_dir, 'server.properties'), 'r', encoding='utf-8') as arq:
        text = arq.read()
    return text


class Type(models.Model):
    name = models.CharField(max_length=100)
    dependencies = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class Server(models.Model):
    name = models.CharField(max_length=100)
    jar_template = models.CharField(max_length=100, choices=get_jar_files())
    jar = models.CharField(max_length=100, blank=True, null=True)
    port = models.IntegerField(default=25565)
    memory_limit = models.IntegerField("Memory Limit (MB)", default=1024)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    type = models.ForeignKey(
        Type, on_delete=models.CASCADE, related_name='servers')
    status = models.BooleanField(default=False)
    server_properties = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name
```

This drops the `uuid` import (was unused), `shutil` (moved to `provisioning.py`), the signal imports (`receiver`, `post_save`, `pre_save`, `pre_delete`), and the two signal handler functions with their `.connect(...)` calls at the bottom of the old file. The `jar_template` field's `choices=get_jar_files()` frozen-at-import bug is untouched here — Task 3 fixes it.

- [ ] **Step 5: Wire provisioning into the admin**

Replace the full contents of `mcmanager/console/admin.py` with:

```python
from typing import Any

from django.contrib import admin

from .models import Server, Type
from .services import provisioning


@admin.register(Server)
class ServerAdmin(admin.ModelAdmin):
    list_display = ('name', 'port', 'type', 'status')
    exclude = ('jar', 'server_properties')
    search_fields = ('name', 'port', 'type')
    list_filter = ('type', 'status')
    readonly_fields = ('status',)

    def get_form(self, request, obj=..., change=..., **kwargs) -> Any:
        if obj is None:
            self.exclude = ('jar', 'server_properties')
            self.readonly_fields = ('status',)
        else:
            self.exclude = None
            self.readonly_fields = ('status', 'jar')
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

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest mcmanager/console/tests/test_provisioning.py -v`
Expected: `6 passed`

- [ ] **Step 7: Run the full suite to check for regressions**

Run: `python -m pytest -v`
Expected: all tests pass (smoke tests from Task 1 plus the six new ones).

- [ ] **Step 8: Commit**

```bash
git add mcmanager/console/services/ mcmanager/console/tests/test_provisioning.py mcmanager/console/models.py mcmanager/console/admin.py
git commit -m "refactor: replace Server model signals with a tested provisioning service"
```

---

### Task 3: Fix the frozen jar_template choices bug

**Files:**
- Create: `mcmanager/console/forms.py`
- Create: `mcmanager/console/tests/test_forms.py`
- Create: `mcmanager/console/migrations/0005_alter_server_jar_template.py` (auto-generated)
- Modify: `mcmanager/console/models.py:19` (the `jar_template` field)
- Modify: `mcmanager/console/admin.py`

**Interfaces:**
- Consumes: `get_jar_files()` from `mcmanager/console/models.py` (unchanged signature: `() -> list[tuple[str, str]]`).
- Produces: `mcmanager.console.forms.ServerForm`, wired as `ServerAdmin.form`. Task 5 does not touch this file.

**Context:** `jar_template = models.CharField(max_length=100, choices=get_jar_files())` calls `get_jar_files()` exactly once, when Python imports `models.py` (i.e., once per process lifetime). A `.jar` file dropped into the jar directory after the panel started won't show up as a choice until the process restarts. The fix moves choice resolution into a `ModelForm.__init__`, which runs fresh on every request.

- [ ] **Step 1: Write the failing test**

Create `mcmanager/console/tests/test_forms.py`:

```python
from mcmanager.console.forms import ServerForm


def test_jar_template_choices_reflect_newly_added_jars(settings, tmp_path):
    settings.JAR_DIR = tmp_path / "jar"
    settings.JAR_DIR.mkdir()

    first_form = ServerForm()
    assert first_form.fields["jar_template"].choices == []

    (settings.JAR_DIR / "fabric.jar").write_bytes(b"fake-jar-bytes")

    second_form = ServerForm()
    choices = second_form.fields["jar_template"].choices
    assert ("fabric.jar", "fabric.jar") in choices
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest mcmanager/console/tests/test_forms.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcmanager.console.forms'`

- [ ] **Step 3: Create the form**

Create `mcmanager/console/forms.py`:

```python
from django import forms

from .models import Server, get_jar_files


class ServerForm(forms.ModelForm):
    jar_template = forms.ChoiceField(choices=[])

    class Meta:
        model = Server
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['jar_template'].choices = get_jar_files()
```

- [ ] **Step 4: Remove the frozen choices from the model field**

In `mcmanager/console/models.py`, change:

```python
    jar_template = models.CharField(max_length=100, choices=get_jar_files())
```

to:

```python
    jar_template = models.CharField(max_length=100)
```

- [ ] **Step 5: Generate the migration**

Run: `python manage.py makemigrations console`
Expected output includes: `Migrations for 'console': mcmanager\console\migrations\0005_alter_server_jar_template.py` (Django names `AlterField` migrations `<n>_alter_<model>_<field>.py`; if the generated name differs, use the actual generated filename for the rest of this task).

- [ ] **Step 6: Wire the form into the admin**

In `mcmanager/console/admin.py`, add the import:

```python
from .forms import ServerForm
```

next to the other local imports, and add `form = ServerForm` as the first line of `ServerAdmin`'s body:

```python
@admin.register(Server)
class ServerAdmin(admin.ModelAdmin):
    form = ServerForm
    list_display = ('name', 'port', 'type', 'status')
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `python -m pytest mcmanager/console/tests/test_forms.py -v`
Expected: `1 passed`

- [ ] **Step 8: Run the full suite to check for regressions**

Run: `python -m pytest -v`
Expected: all tests pass.

- [ ] **Step 9: Commit**

```bash
git add mcmanager/console/forms.py mcmanager/console/tests/test_forms.py mcmanager/console/models.py mcmanager/console/admin.py mcmanager/console/migrations/0005_alter_server_jar_template.py
git commit -m "fix: resolve jar_template choices per-request instead of at import time"
```

---

### Task 4: Remove the command-injection risk in force_stop_server

**Files:**
- Modify: `mcmanager/console/views.py:18-24` (delete `is_server_running_v2`)
- Modify: `mcmanager/console/views.py:73-88` (rewrite `force_stop_server`)
- Create: `mcmanager/console/tests/test_force_stop.py`

**Interfaces:**
- Consumes: `Server.jar`, `is_server_running` (unchanged, `mcmanager/console/views.py:27-38`), the existing `/tmp/minecraft_server_<id>.pid` / `.pty` file convention.
- Produces: a `force_stop_server` view with the same URL, same POST/GET behavior *at this point in the plan* (Task 5 adds the `@require_POST` / `@staff_member_required` decorators), and the same JSON contract, but killing processes via `psutil` PID lookup instead of `os.popen(f"pkill -f {server.jar}")`.

**Context:** `is_server_running_v2` (`os.popen(f"pgrep -f {server.jar}")`) is dead code — confirmed via repo-wide search, nothing calls it. `force_stop_server` shells out to `pkill -f {server.jar}`, interpolating a filename straight into a shell command string. `server.jar` is built from `server.id` and `jar_template`, which is itself a filename from the jar directory — not directly attacker-controlled today, but it is exactly the kind of string-into-shell pattern that turns into a real vulnerability the moment `jar_template` handling changes (e.g., Phase 3's jar download feature). Replacing it with PID-based `psutil` lookup — the same pattern `is_server_running` already uses — removes the shell entirely.

- [ ] **Step 1: Write the failing test**

Create `mcmanager/console/tests/test_force_stop.py`:

```python
import os
import subprocess
import sys

import psutil
import pytest
from django.test import Client

from mcmanager.console.models import Server, Type


@pytest.mark.django_db
@pytest.mark.skipif(os.name != "posix", reason="force_stop_server uses /tmp PID files (POSIX-only until Phase 2)")
def test_force_stop_server_kills_process_without_shelling_out():
    server_type = Type.objects.create(name="Vanilla")
    server = Server.objects.create(
        name="Test", jar_template="paper.jar", jar="1_paper.jar", type=server_type
    )

    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    pid_file = f"/tmp/minecraft_server_{server.id}.pid"
    with open(pid_file, "w", encoding="utf-8") as f:
        f.write(str(proc.pid))

    try:
        client = Client()
        response = client.get(f"/console/force_stop_server/{server.id}")

        assert response.status_code == 200
        assert response.json()["status"] == "success"
        proc.wait(timeout=5)
        assert not psutil.pid_exists(proc.pid)
    finally:
        if os.path.exists(pid_file):
            os.remove(pid_file)
        if proc.poll() is None:
            proc.kill()
```

Note: this test spawns a plain Python subprocess (not a Minecraft server) whose command line does **not** contain the string `"1_paper.jar"`. Against the *old* `pkill -f {server.jar}` implementation this test fails (the pattern doesn't match the process, so it survives) — that failure is the correct "red" state for this task, proving the old approach doesn't reliably kill the tracked process. On non-POSIX systems (this task doesn't touch the `/tmp`-based PID path — that's Phase 2), the test is skipped.

- [ ] **Step 2: Run it to verify it fails (Linux) or is skipped (Windows)**

Run: `python -m pytest mcmanager/console/tests/test_force_stop.py -v`
Expected on Linux: FAIL — the spawned process is still alive after calling `force_stop_server` because `pkill -f 1_paper.jar` doesn't match its command line.
Expected on Windows: `1 skipped`.

- [ ] **Step 3: Delete the dead is_server_running_v2 function**

In `mcmanager/console/views.py`, delete lines 18-24:

```python
def is_server_running_v2(id):
    server = Server.objects.get(id=id)
    result = os.popen(f"pgrep -f {server.jar}").read().strip()
    if result:
        print("PID: ", result)
        return True
    return False


```

- [ ] **Step 4: Rewrite force_stop_server**

Replace the `force_stop_server` function body with:

```python
def force_stop_server(request, id):
    server = Server.objects.get(id=id)
    SERVER_PID_FILE = f'/tmp/minecraft_server_{id}.pid'
    SERVER_PTY_FILE = f'/tmp/minecraft_server_{id}.pty'
    try:
        if os.path.exists(SERVER_PID_FILE):
            with open(SERVER_PID_FILE, 'r') as f:
                pid = int(f.read().strip())
            try:
                psutil.Process(pid).kill()
            except psutil.NoSuchProcess:
                pass
        for path in (SERVER_PID_FILE, SERVER_PTY_FILE):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
        server.status = False
        server.save()
        return JsonResponse({'status': 'success', 'message': 'Server stopped'})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)})
```

`psutil` is already imported at the top of `views.py` (line 7) — no import changes needed.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest mcmanager/console/tests/test_force_stop.py -v`
Expected on Linux: `1 passed`. On Windows: `1 skipped`.

- [ ] **Step 6: Run the full suite to check for regressions**

Run: `python -m pytest -v`
Expected: all tests pass (or skip, on Windows, for the POSIX-only one).

- [ ] **Step 7: Commit**

```bash
git add mcmanager/console/views.py mcmanager/console/tests/test_force_stop.py
git commit -m "fix: kill server processes via psutil PID lookup instead of shelling out to pkill"
```

---

### Task 5: Authentication, POST-only, and CSRF enforcement on every control view

**Files:**
- Modify: `mcmanager/console/views.py` (full file — see Step 3 for target content)
- Modify: `mcmanager/console/templates/console/index.html` (Step 4)
- Create: `mcmanager/console/tests/test_view_permissions.py`

**Interfaces:**
- Consumes: `is_server_running`, `force_stop_server` (Task 4), `Server` model. No changes to URL names (`mcmanager/console/urls.py` untouched) or JSON response shapes.
- Produces: every control view requires a logged-in staff user; `start_server`, `stop_server`, `force_stop_server`, `send_command` require `POST`; `send_command` requires a valid CSRF token (the `@csrf_exempt` bypass is removed). This is the last task of Phase 1 — nothing downstream in this plan builds on it.

**Context — current gaps:**
- `start_server`, `stop_server`, `force_stop_server`, `view_logs`, `get_server_stats`, `home` have no `@staff_member_required` — anyone who can reach the panel's network can start/stop servers or read logs.
- `start_server`, `stop_server`, `force_stop_server` are triggered from the template via a plain `fetch(url)` (GET) with no CSRF protection — a malicious page could stop/start a server just by getting an authenticated admin's browser to load an `<img src="...">` pointing at that URL.
- `send_command` is `@csrf_exempt`, defeating Django's CSRF protection entirely for the one endpoint that executes arbitrary server console commands.

- [ ] **Step 1: Write the failing tests**

Create `mcmanager/console/tests/test_view_permissions.py`:

```python
import pytest
from django.test import Client
from django.urls import reverse

from mcmanager.console.models import Server, Type


@pytest.fixture
def server(db):
    server_type = Type.objects.create(name="Vanilla")
    return Server.objects.create(
        name="Test", jar_template="paper.jar", jar="1_paper.jar", type=server_type
    )


@pytest.fixture
def staff_user(django_user_model):
    return django_user_model.objects.create_user(
        username="admin", password="pw", is_staff=True
    )


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", ["index", "view_logs", "get_server_stats"])
def test_get_only_control_views_require_staff_login(client, server, url_name):
    response = client.get(reverse(url_name, kwargs={"id": server.id}))
    assert response.status_code == 302


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", ["start_server", "stop_server", "force_stop_server"])
def test_mutating_views_require_staff_login(client, server, url_name):
    response = client.post(reverse(url_name, kwargs={"id": server.id}))
    assert response.status_code == 302


@pytest.mark.django_db
def test_home_requires_staff_login(client):
    response = client.get(reverse("home"))
    assert response.status_code == 302


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", ["start_server", "stop_server", "force_stop_server"])
def test_mutating_views_reject_get_from_staff_user(client, server, staff_user, url_name):
    client.force_login(staff_user)
    response = client.get(reverse(url_name, kwargs={"id": server.id}))
    assert response.status_code == 405


@pytest.mark.django_db
def test_send_command_rejects_missing_csrf_token(server, staff_user):
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(staff_user)
    response = csrf_client.post(
        reverse("send_command", kwargs={"id": server.id}), {"command": "say hi"}
    )
    assert response.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest mcmanager/console/tests/test_view_permissions.py -v`
Expected: FAIL — `test_get_only_control_views_require_staff_login[view_logs]`, `[get_server_stats]`, `test_mutating_views_require_staff_login[*]`, `test_home_requires_staff_login`, `test_mutating_views_reject_get_from_staff_user[*]`, and `test_send_command_rejects_missing_csrf_token` all fail because the views aren't decorated yet.

- [ ] **Step 3: Add auth, POST-only, and remove csrf_exempt**

Replace the full contents of `mcmanager/console/views.py` with:

```python
import os
try:
    import pty
except ImportError:
    pty = None
import psutil
from django.http import JsonResponse, HttpRequest
from django.shortcuts import render, redirect
from django.views.decorators.http import require_POST
from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from .models import Server

LOG_FILE = 'logs/latest.log'


def is_server_running(id):
    SERVER_PID_FILE = f'/tmp/minecraft_server_{id}.pid'
    if os.path.exists(SERVER_PID_FILE):
        with open(SERVER_PID_FILE, 'r') as f:
            pid = int(f.read().strip())
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                pass
    return False


@staff_member_required
def index(request, id):
    server = Server.objects.get(id=id)
    server_running = is_server_running(id)
    return render(request, 'console/index.html', {'server_running': server_running, 'server': server})


@staff_member_required
@require_POST
def start_server(request, id):
    if pty is None:
        return JsonResponse({'status': 'error', 'message': 'PTY is not supported on this platform (Linux required)'})
    server = Server.objects.get(id=id)
    SERVER_COMMAND = f"{settings.JAVA_BIN_PATH} -Xms{server.memory_limit}M -Xmx{server.memory_limit}M -jar {server.jar}"
    servers_dir = getattr(settings, 'SERVERS_DIR', os.path.join(settings.BASE_DIR, 'servers'))
    SERVER_DIRECTORY = os.path.join(servers_dir, f'server_{server.id}')
    SERVER_PID_FILE = f'/tmp/minecraft_server_{id}.pid'
    SERVER_PTY_FILE = f'/tmp/minecraft_server_{id}.pty'
    if not is_server_running(id):
        os.chdir(SERVER_DIRECTORY)
        pid, fd = pty.fork()
        if pid == 0:
            os.execv('/bin/sh', ['/bin/sh', '-c', SERVER_COMMAND])
        with open(SERVER_PID_FILE, 'w') as f:
            f.write(str(pid))
        with open(SERVER_PTY_FILE, 'w') as f:
            f.write(str(fd))
        server.status = True
        server.save()
        return JsonResponse({'status': 'success', 'message': 'Server started'})
    else:
        return JsonResponse({'status': 'error', 'message': 'Server is already running'})


@staff_member_required
@require_POST
def force_stop_server(request, id):
    server = Server.objects.get(id=id)
    SERVER_PID_FILE = f'/tmp/minecraft_server_{id}.pid'
    SERVER_PTY_FILE = f'/tmp/minecraft_server_{id}.pty'
    try:
        if os.path.exists(SERVER_PID_FILE):
            with open(SERVER_PID_FILE, 'r') as f:
                pid = int(f.read().strip())
            try:
                psutil.Process(pid).kill()
            except psutil.NoSuchProcess:
                pass
        for path in (SERVER_PID_FILE, SERVER_PTY_FILE):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
        server.status = False
        server.save()
        return JsonResponse({'status': 'success', 'message': 'Server stopped'})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)})


@staff_member_required
@require_POST
def stop_server(request, id):
    server = Server.objects.get(id=id)
    SERVER_PID_FILE = f'/tmp/minecraft_server_{id}.pid'
    SERVER_PTY_FILE = f'/tmp/minecraft_server_{id}.pty'
    if is_server_running(id):
        with open(SERVER_PID_FILE, 'r') as f:
            pid = int(f.read().strip())
            os.kill(pid, 15)
        os.remove(SERVER_PID_FILE)
        os.remove(SERVER_PTY_FILE)
        server.status = False
        server.save()
        return JsonResponse({'status': 'success', 'message': 'Server stopped'})
    else:
        return JsonResponse({'status': 'error', 'message': 'Server is not running'})


@staff_member_required
def view_logs(request, id):
    server = Server.objects.get(id=id)
    servers_dir = getattr(settings, 'SERVERS_DIR', os.path.join(settings.BASE_DIR, 'servers'))
    SERVER_DIRECTORY = os.path.join(servers_dir, f'server_{server.id}')
    if os.path.exists(os.path.join(SERVER_DIRECTORY, LOG_FILE)):
        with open(os.path.join(SERVER_DIRECTORY, LOG_FILE), 'r', encoding="utf8", errors='ignore') as f:
            logs = f.read()
        return JsonResponse({'status': 'success', 'logs': logs})
    else:
        return JsonResponse({'status': 'error', 'message': 'Log file not found'})


@staff_member_required
@require_POST
def send_command(request, id):
    SERVER_PTY_FILE = f'/tmp/minecraft_server_{id}.pty'
    command = request.POST.get('command')
    if is_server_running(id):
        try:
            with open(SERVER_PTY_FILE, 'r') as f:
                fd = int(f.read().strip())
                os.write(fd, f'{command}\n'.encode())
                return JsonResponse({'status': 'success', 'message': 'Command sent'})
        except OSError as e:
            return JsonResponse({'status': 'error', 'message': str(e)})
    return JsonResponse({'status': 'error', 'message': 'Server is not running'})


@staff_member_required
def get_server_stats(request, id):
    SERVER_PID_FILE = f'/tmp/minecraft_server_{id}.pid'
    if is_server_running(id):
        with open(SERVER_PID_FILE, 'r') as f:
            pid = int(f.read().strip())
            try:
                process = psutil.Process(pid)

                cpu_usage = process.cpu_percent(interval=1)
                memory_info = process.memory_info()
                memory_usage = memory_info.rss / (1024 * 1024)

                virtual_memory = psutil.virtual_memory()
                total_memory = virtual_memory.total / (1024 * 1024)
                used_memory = virtual_memory.used / (1024 * 1024)

                total_cpu_usage = psutil.cpu_percent(interval=1)
                return JsonResponse({
                    'status': 'success',
                    'cpu_usage': cpu_usage,
                    'memory_usage': memory_usage,
                    'total_memory': total_memory,
                    'used_memory': used_memory,
                    'total_cpu_usage': total_cpu_usage,
                })
            except psutil.NoSuchProcess:
                return JsonResponse({'status': 'error', 'message': 'Process not found'})
    return JsonResponse({'status': 'error', 'message': 'Server is not running'})


@staff_member_required
def home(request: HttpRequest):
    ctx = {
        "servers": Server.objects.all()
    }
    if request.method == 'POST':
        server_id = request.POST.get('server_id')
        return redirect('index', id=server_id)
    return render(request, 'index.html', ctx)
```

Compared to the file as it stands after Task 4, this: removes the `from django.views.decorators.csrf import csrf_exempt` import (no longer used); adds `from django.views.decorators.http import require_POST`; adds `@staff_member_required` to `start_server`, `force_stop_server`, `stop_server`, `view_logs`, `get_server_stats`, `home`; adds `@require_POST` to `start_server`, `force_stop_server`, `stop_server`, `send_command`; removes `@csrf_exempt` and the manual `if request.method == 'POST':` guard from `send_command` (the decorator now does that check and returns a proper 405 otherwise).

- [ ] **Step 4: Update the template to POST with a CSRF header for start/stop/force-stop**

In `mcmanager/console/templates/console/index.html`, replace the `startServer`, `stopServer`, and `forceStopServer` JavaScript functions (currently around lines 148-182) with:

```javascript
            function getCsrfToken() {
                return document.querySelector('[name=csrfmiddlewaretoken]').value;
            }

            function postJSON(url) {
                return fetch(url, {
                    method: 'POST',
                    headers: { 'X-CSRFToken': getCsrfToken() }
                }).then(response => response.json());
            }

            function startServer() {
                postJSON("{% url 'start_server' id=server.id %}")
                    .then(data => {
                        create_toast(data.message, 'green', 'white');
                        if (data.status === 'success') {
                            location.reload();
                        }
                    });
            }

            function stopServer() {
                postJSON("{% url 'stop_server' id=server.id %}")
                    .then(data => {
                        create_toast(data.message, 'red', 'white');
                        if (data.status === 'success') {
                            location.reload();
                        }
                    });
            }

            function forceStopServer() {
                postJSON("{% url 'force_stop_server' id=server.id %}")
                    .then(data => {
                        create_toast(data.message, 'red', 'white');
                        if (data.status === 'success') {
                            location.reload();
                        }
                    });
            }
```

`getCsrfToken()` reads the hidden `csrfmiddlewaretoken` input that the `{% csrf_token %}` tag already renders inside `#command-form` earlier in this same template — no new template markup is needed.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest mcmanager/console/tests/test_view_permissions.py -v`
Expected: `9 passed`

- [ ] **Step 6: Run the full suite to check for regressions**

Run: `python -m pytest -v`
Expected: all tests pass (or skip, on Windows, for the Task 4 POSIX-only test).

- [ ] **Step 7: Manually verify the golden path in a browser**

This step touches product UI behavior that the automated suite doesn't cover (real button clicks, real toasts). On a Linux machine (or any POSIX environment with Java available):

```bash
python manage.py createsuperuser
python manage.py runserver
```

Log in at `/admin/`, create a `Type` and a `Server`, open `/console/<id>`, and confirm: Start Server / Stop Server / Force Stop Server buttons still work (now firing POST requests — check the Network tab to confirm `Request Method: POST` and a `X-CSRFToken` header), the command form still sends commands, and logs still refresh every 2.5s.

- [ ] **Step 8: Commit**

```bash
git add mcmanager/console/views.py mcmanager/console/templates/console/index.html mcmanager/console/tests/test_view_permissions.py
git commit -m "feat: require staff login and POST for all server control endpoints"
```

---

## Phase 1 exit check

After Task 5's commit, re-run the full suite once more (`python -m pytest -v`) and confirm:
- No view is reachable by an anonymous user.
- No mutating endpoint is reachable via GET.
- `send_command` enforces CSRF.
- No `os.popen`/shell-based process lookup remains in `mcmanager/console/views.py`.
- Server creation/edit/delete through the Django admin still provisions/updates/removes files on disk exactly as before.

This closes out Phase 1 of `docs/design-melhorias.md`. Phase 2 (cross-platform process manager) is a separate plan.
