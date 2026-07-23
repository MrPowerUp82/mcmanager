# Java Launch Output Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop discarding the Java process's stdout/stderr in `process.start()`, capture it to a per-server file instead, and let staff view the last launch attempt's raw output from the console page.

**Architecture:** `process.start()` redirects `stdout`/`stderr` to a file (`server_dir/logs/last_start_output.log`, overwritten on every start) instead of `subprocess.DEVNULL`. A new `process.read_launch_output(server)` reads it back. A new read-only view/URL exposes it as JSON; a new button on the console page fetches and displays it.

**Tech Stack:** stdlib only (`pathlib`, `subprocess`) — no new dependency.

## Global Constraints

- The capture file is `server_dir/logs/last_start_output.log`, distinct from Minecraft's own `logs/latest.log` — never conflated or merged with it.
- The file is **overwritten** on every `start()` call, never appended to.
- `stdout` and `stderr` are combined into the same file, in real chronological order (`stderr=subprocess.STDOUT` redirected into the same file handle used for `stdout`).
- `process.start()` must create `server_dir/logs/` itself if it doesn't exist yet, before opening the capture file — do not rely on Minecraft to create that directory.
- No parsing/interpretation of known error patterns (e.g. detecting `UnsupportedClassVersionError` specifically) — the raw captured text is shown as-is.
- The new view is read-only (`GET`, no `@require_POST`), `@staff_member_required`, and uses the existing `json_utils.json_error(message, status=404)` helper for the not-yet-captured case — matching the project's established error-response pattern.
- The new "Ver saída de erro" button on the console page is always visible, not conditional on server state.

---

### Task 1: Capture launch output in `process.start()`

**Files:**
- Modify: `mcmanager/console/services/process.py:108-136` (the `start()` function) and add a new `read_launch_output()` function
- Test: `mcmanager/console/tests/test_process.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `process.read_launch_output(server)` — takes a `Server` instance, returns the file's text content as a `str`, or `None` if no capture exists yet. Task 2's view calls this directly.

- [ ] **Step 1: Write the failing tests**

Add `import stat` to the top of `mcmanager/console/tests/test_process.py`, alongside the existing `import itertools`/`import os`/`import socket`/`import time` block (`time`, `os`, `pytest`, `Path`, `patch`, the `server`/`fake_java`/`server_dirs`/`settings` fixtures, and `process` are already available in this file — only `stat` is new).

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest mcmanager/console/tests/test_process.py -v -k "launch_output or logs_directory"`
Expected: FAIL — `test_read_launch_output_returns_none_when_never_started` fails with `AttributeError: module 'mcmanager.console.services.process' has no attribute 'read_launch_output'`; the others fail because `start()` still uses `DEVNULL` (the "logs directory" test fails because nothing creates `logs/` yet).

- [ ] **Step 3: Modify `process.start()` and add `read_launch_output()`**

In `mcmanager/console/services/process.py`, replace the `start()` function body:

```python
def start(server):
    if is_running(server):
        raise AlreadyRunningError(f'Server {server.id} is already running')

    _check_port_available(server.port)

    if is_jar_missing(server):
        raise JarMissingError(f'Jar file missing for server {server.id}: {_jar_path(server)}')

    server_dir = settings.SERVERS_DIR / f'server_{server.id}'
    logs_dir = server_dir / 'logs'
    logs_dir.mkdir(parents=True, exist_ok=True)
    launch_output_path = logs_dir / 'last_start_output.log'

    cmd = [
        settings.JAVA_BIN_PATH,
        f'-Xms{server.memory_limit}M',
        f'-Xmx{server.memory_limit}M',
        '-jar', server.jar,
        'nogui',
    ]
    with launch_output_path.open('wb') as launch_output_file:
        kwargs = {
            'cwd': str(server_dir),
            'stdout': launch_output_file,
            'stderr': subprocess.STDOUT,
        }
        if os.name == 'posix':
            kwargs['start_new_session'] = True
        else:
            kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP

        try:
            proc = subprocess.Popen(cmd, **kwargs)
        except FileNotFoundError as exc:
            raise JavaNotFoundError(f'Java binary not found: {settings.JAVA_BIN_PATH}') from exc

    _write_state(server, proc.pid)
```

Add this new function anywhere below `_jar_path` (e.g. right after `is_jar_missing`):

```python
def read_launch_output(server):
    path = settings.SERVERS_DIR / f'server_{server.id}' / 'logs' / 'last_start_output.log'
    if not path.exists():
        return None
    return path.read_text(encoding='utf-8', errors='replace')
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest mcmanager/console/tests/test_process.py -v`
Expected: all tests pass, including the pre-existing ones (this change doesn't alter `start()`'s exception-raising behavior, only what happens to stdout/stderr on success)

- [ ] **Step 5: Commit**

```bash
git add mcmanager/console/services/process.py mcmanager/console/tests/test_process.py
git commit -m "feat: capture Java process stdout/stderr to a file instead of discarding it"
```

---

### Task 2: `launch_output` view, URL, and console page button

**Files:**
- Modify: `mcmanager/console/views.py`
- Modify: `mcmanager/console/urls.py`
- Modify: `mcmanager/console/templates/console/index.html`
- Test: `mcmanager/console/tests/test_launch_output_view.py`

**Interfaces:**
- Consumes: `process.read_launch_output(server)` (Task 1) — returns `str` or `None`.
- Produces: nothing consumed by later tasks (final task in this plan).

- [ ] **Step 1: Write the failing tests**

Create `mcmanager/console/tests/test_launch_output_view.py`:

```python
from unittest.mock import patch

import pytest
from django.urls import reverse

from mcmanager.console.models import Server, Type


@pytest.fixture
def staff_client(client, django_user_model):
    staff = django_user_model.objects.create_user(username="admin", password="pw", is_staff=True)
    client.force_login(staff)
    return client


@pytest.fixture
def server(db):
    server_type = Type.objects.create(name="Vanilla")
    return Server.objects.create(name="Test", jar_template="paper.jar", type=server_type)


@pytest.mark.django_db
def test_launch_output_requires_staff_login(client, server):
    resp = client.get(reverse("launch_output", args=[server.id]))
    assert resp.status_code in (302, 403)


@pytest.mark.django_db
def test_launch_output_returns_captured_content(staff_client, server):
    with patch(
        "mcmanager.console.views.process.read_launch_output",
        return_value="FAKE JAVA ERROR: test crash message\n",
    ):
        resp = staff_client.get(reverse("launch_output", args=[server.id]))

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert "FAKE JAVA ERROR" in body["output"]


@pytest.mark.django_db
def test_launch_output_returns_404_when_not_captured_yet(staff_client, server):
    with patch("mcmanager.console.views.process.read_launch_output", return_value=None):
        resp = staff_client.get(reverse("launch_output", args=[server.id]))

    assert resp.status_code == 404
    assert resp.json()["status"] == "error"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest mcmanager/console/tests/test_launch_output_view.py -v`
Expected: FAIL with `NoReverseMatch: Reverse for 'launch_output' not found`

- [ ] **Step 3: Add the view**

In `mcmanager/console/views.py`, add this new view function anywhere after `view_logs` (e.g. right after it):

```python
@staff_member_required
def launch_output(request, id):
    server = Server.objects.get(id=id)
    output = process.read_launch_output(server)
    if output is None:
        return json_error('No launch output captured yet', status=404)
    return JsonResponse({'status': 'success', 'output': output})
```

- [ ] **Step 4: Add the URL**

In `mcmanager/console/urls.py`, add this line to `urlpatterns` (e.g. right after the `view_logs` line):

```python
    path('launch_output/<int:id>', views.launch_output, name='launch_output'),
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest mcmanager/console/tests/test_launch_output_view.py -v`
Expected: all 3 tests pass

- [ ] **Step 6: Add the console page button**

In `mcmanager/console/templates/console/index.html`, add a new button right after the existing "View Logs" button (inside the same `<div class="mb-4">` block):

```html
                <button onclick="viewLogs()"
                        class="bg-green-500 hover:bg-green-700 text-white font-bold py-2 px-4 rounded ml-2">
                    View Logs
                </button>
                <button onclick="viewLaunchOutput()"
                        class="bg-yellow-500 hover:bg-yellow-700 text-white font-bold py-2 px-4 rounded ml-2">
                    Ver saída de erro
                </button>
```

Add a new display panel right after the existing `<div id="logs" ...></div>` block (before `<div id="stats" ...>`):

```html
            <div id="logs"
                 class="bg-white p-4 border rounded shadow-md overflow-y-auto overflow-x-hidden"
                 style="white-space: pre-wrap;
                        max-height: 400px"></div>
            <div id="launch-output"
                 class="bg-white p-4 border rounded shadow-md overflow-y-auto overflow-x-hidden mt-2"
                 style="white-space: pre-wrap;
                        max-height: 400px"></div>
            <div id="stats" class="mt-4"></div>
```

Add this new JS function inside the existing `<script>` block, anywhere alongside the other view-related functions like `viewLogs()` (e.g. right after `viewLogs`'s closing brace):

```javascript
            function viewLaunchOutput() {
                fetch("{% url 'launch_output' id=server.id %}")
                    .then(response => response.json())
                    .then(data => {
                        const panel = document.getElementById('launch-output');
                        if (data.status === 'success') {
                            panel.textContent = data.output;
                        } else {
                            panel.textContent = data.message;
                        }
                    });
            }
```

- [ ] **Step 7: Run the full test suite**

Run: `python -m pytest -v`
Expected: all tests pass (the pre-existing, documented SQLite lock-contention flake may intermittently affect unrelated tests in `test_jars.py`/`test_backups.py` — if that's the only failure, re-run just that file to confirm it's the known flake, not a regression from this task's diff).

- [ ] **Step 8: Manually verify in a browser**

Start a server with a Java binary that will fail (or reuse the real incompatible-Java scenario if convenient), then open its console page and click "Ver saída de erro" — confirm the captured output appears in the new panel. Also click it for a server that started successfully — confirm it shows the successful startup output (not an error), proving the button works regardless of outcome, not just on crash.

- [ ] **Step 9: Commit**

```bash
git add mcmanager/console/views.py mcmanager/console/urls.py mcmanager/console/templates/console/index.html mcmanager/console/tests/test_launch_output_view.py
git commit -m "feat: add a console page button to view the last Java launch attempt's raw output"
```
