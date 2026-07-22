# Structured Errors & Edge Cases Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add correct HTTP status codes to every error JSON response across the panel, detect a port already in use before starting a server, and detect a missing jar file (surfaced as a "broken" dashboard status).

**Architecture:** A small `json_error(message, status=400)` helper centralizes error-response construction; every existing error branch across `views.py`, `views_jars.py`, `views_backups.py` switches to it with the correct status code. Two new pre-flight checks are added to `process.start()` (port availability via `socket.bind()`, jar existence via a plain path check), each with its own exception type that the `start_server` view translates to HTTP 409. The jar-existence check is also exposed as `process.is_jar_missing()`, consumed by the existing `services/dashboard.py` so a broken server shows a distinct badge even without attempting to start it.

**Tech Stack:** `socket` (stdlib, cross-platform port check), plain `pathlib`/`Path.exists()` (jar check) — no new dependency.

## Global Constraints

- Success-response JSON shape does NOT change — every existing key at the top level of a successful response stays exactly where it is today. Only error responses change (they gain a correct `status=` HTTP code on the `JsonResponse`).
- Every error-response call site listed in the table below must use the new `json_error(message, status=...)` helper with the exact status code specified — no other status code substitutions.
- `PortInUseError` and `JarMissingError` are both new exception classes in `mcmanager/console/services/process.py`, both translated to HTTP 409 by the `start_server` view (a conflict between the requested action and the current environment/server state).
- `is_jar_missing(server)` must never raise — a server with no `jar` set at all (e.g. never provisioned) counts as missing, not an error.
- No other view behavior changes — this plan only touches status codes and the two new pre-flight checks; it does not restructure success payloads, add new success fields (other than dashboard's new `jar_missing` field), or change any URL.

### HTTP status code table (binding for every task)

| View | Condition | Status |
|------|-----------|--------|
| `start_server` | `AlreadyRunningError` | 409 |
| `start_server` | `JavaNotFoundError` | 503 |
| `start_server` | `PortInUseError` (new, Task 3) | 409 |
| `start_server` | `JarMissingError` (new, Task 4) | 409 |
| `stop_server` | `ProcessNotRunningError` | 409 |
| `stop_server` | `StopTimeoutError` | 504 |
| `stop_server` | `RconError` | 502 |
| `view_logs` | log file not found | 404 |
| `send_command` | command missing | 400 |
| `send_command` | `ProcessNotRunningError` | 409 |
| `send_command` | `RconError` | 502 |
| `get_server_stats` | `ProcessNotRunningError` | 409 |
| `list_jar_versions` | unknown provider | 400 |
| `list_jar_versions` | provider raised an exception | 502 |
| `start_jar_download` | invalid provider/version | 400 |
| `jar_download_status` | download not found | 404 |
| `backup_status_view` | backup not found | 404 |
| `restore_backup_view` | filename missing | 400 |
| `restore_backup_view` | restore raised an exception | 409 |
| `delete_backup_view` | filename missing | 400 |
| `delete_backup_view` | delete raised an exception | 409 |

`force_stop_server` and `list_backups_view` have no error branch today and are unchanged by this plan.

---

### Task 1: `json_error()` helper + `console/views.py` status codes

**Files:**
- Create: `mcmanager/console/json_utils.py`
- Modify: `mcmanager/console/views.py`
- Modify: `mcmanager/console/tests/test_view_logs.py`
- Modify: `mcmanager/console/tests/test_views_start_stop_e2e.py`
- Test: `mcmanager/console/tests/test_json_utils.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `json_utils.json_error(message, status=400)` — returns a `JsonResponse({'status': 'error', 'message': message}, status=status)`. Task 2 imports and uses this same function from `views_jars.py`/`views_backups.py`.

- [ ] **Step 1: Write the failing test for the helper**

Create `mcmanager/console/tests/test_json_utils.py`:

```python
from mcmanager.console.json_utils import json_error


def test_json_error_sets_status_and_http_code():
    response = json_error('Something broke', status=503)
    assert response.status_code == 503
    body = response.json() if hasattr(response, 'json') else __import__('json').loads(response.content)
    assert body['status'] == 'error'
    assert body['message'] == 'Something broke'


def test_json_error_defaults_to_400():
    response = json_error('Bad input')
    assert response.status_code == 400
```

(`JsonResponse` objects don't have a `.json()` method — use `import json; json.loads(response.content)` instead. Simplify the first test to avoid the `hasattr` branch entirely:)

```python
import json

from mcmanager.console.json_utils import json_error


def test_json_error_sets_status_and_http_code():
    response = json_error('Something broke', status=503)
    assert response.status_code == 503
    body = json.loads(response.content)
    assert body['status'] == 'error'
    assert body['message'] == 'Something broke'


def test_json_error_defaults_to_400():
    response = json_error('Bad input')
    assert response.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest mcmanager/console/tests/test_json_utils.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcmanager.console.json_utils'`

- [ ] **Step 3: Create `mcmanager/console/json_utils.py`**

```python
"""Shared JSON error-response helper. Success responses keep their existing,
per-view flat shape unchanged — only error responses use this, so they get a
real HTTP status code instead of always returning 200."""
from django.http import JsonResponse


def json_error(message, status=400):
    return JsonResponse({'status': 'error', 'message': message}, status=status)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest mcmanager/console/tests/test_json_utils.py -v`
Expected: PASS, both tests

- [ ] **Step 5: Retrofit `mcmanager/console/views.py`**

Add this import line alongside the existing ones at the top of the file:

```python
from .json_utils import json_error
```

Replace `start_server`:

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
        return json_error('Server is already running', status=409)
    except process.JavaNotFoundError as e:
        return json_error(str(e), status=503)
```

(This task does NOT add `PortInUseError`/`JarMissingError` branches — those are added by Tasks 3 and 4, which don't exist yet in `process.py`.)

Replace `stop_server`:

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
        return json_error('Server is not running', status=409)
    except process.StopTimeoutError as e:
        return json_error(str(e), status=504)
    except rcon.RconError as e:
        return json_error(str(e), status=502)
```

Replace the `view_logs` "not found" branch:

```python
    if not log_path.exists():
        return json_error('Log file not found', status=404)
```

Replace `send_command`:

```python
@staff_member_required
@require_POST
def send_command(request, id):
    server = Server.objects.get(id=id)
    command = request.POST.get('command')
    if not command:
        return json_error('No command provided', status=400)
    try:
        response = process.send_command(server, command)
        return JsonResponse({'status': 'success', 'message': response or 'Command sent'})
    except process.ProcessNotRunningError:
        return json_error('Server is not running', status=409)
    except rcon.RconError as e:
        return json_error(str(e), status=502)
```

Replace `get_server_stats`:

```python
@staff_member_required
def get_server_stats(request, id):
    server = Server.objects.get(id=id)
    try:
        stats = process.get_stats(server)
        return JsonResponse({'status': 'success', **stats})
    except process.ProcessNotRunningError:
        return json_error('Server is not running', status=409)
```

- [ ] **Step 6: Update existing tests that assumed error responses were HTTP 200**

In `mcmanager/console/tests/test_view_logs.py`, change:

```python
@pytest.mark.django_db
def test_view_logs_missing_file_returns_error(settings, staff_client, tmp_path):
    settings.SERVERS_DIR = tmp_path / "servers"
    settings.SERVERS_DIR.mkdir()
    server_type = Type.objects.create(name="Vanilla")
    server = Server.objects.create(name="NoLog", jar_template="paper.jar", jar="2_paper.jar", port=25567, type=server_type)

    resp = staff_client.get(f"/console/view_logs/{server.id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "error"
    assert body["message"] == "Log file not found"
```

to:

```python
@pytest.mark.django_db
def test_view_logs_missing_file_returns_error(settings, staff_client, tmp_path):
    settings.SERVERS_DIR = tmp_path / "servers"
    settings.SERVERS_DIR.mkdir()
    server_type = Type.objects.create(name="Vanilla")
    server = Server.objects.create(name="NoLog", jar_template="paper.jar", jar="2_paper.jar", port=25567, type=server_type)

    resp = staff_client.get(f"/console/view_logs/{server.id}")

    assert resp.status_code == 404
    body = resp.json()
    assert body["status"] == "error"
    assert body["message"] == "Log file not found"
```

In `mcmanager/console/tests/test_views_start_stop_e2e.py`, change these three assertions:

```python
@pytest.mark.django_db
def test_start_server_already_running_returns_error(staff_client, provisioned_server):
    server = provisioned_server
    staff_client.post(f"/console/start_server/{server.id}")
    try:
        resp = staff_client.post(f"/console/start_server/{server.id}")
        assert resp.status_code == 409
        assert resp.json()["status"] == "error"
    finally:
        process.force_stop(server)


@pytest.mark.django_db
def test_stop_server_when_not_running_returns_error(staff_client, provisioned_server):
    resp = staff_client.post(f"/console/stop_server/{provisioned_server.id}")
    assert resp.status_code == 409
    assert resp.json()["status"] == "error"


@pytest.mark.django_db
def test_send_command_without_command_param_returns_clean_error(staff_client, provisioned_server):
    server = provisioned_server
    staff_client.post(f"/console/start_server/{server.id}")
    try:
        resp = staff_client.post(f"/console/send_command/{server.id}", {})
        assert resp.status_code == 400
        body = resp.json()
        assert body["status"] == "error"
    finally:
        process.force_stop(server)
```

(Only the `status_code` values change on these three tests — `409`, `409`, `400` respectively, replacing the old `200`. Every other test in this file, and in `test_views_desired_running.py`, is a success-path assertion and needs no change.)

- [ ] **Step 7: Run the affected test files**

Run: `python -m pytest mcmanager/console/tests/test_json_utils.py mcmanager/console/tests/test_view_logs.py mcmanager/console/tests/test_views_start_stop_e2e.py mcmanager/console/tests/test_views_desired_running.py -v`
Expected: all pass

- [ ] **Step 8: Commit**

```bash
git add mcmanager/console/json_utils.py mcmanager/console/views.py mcmanager/console/tests/test_json_utils.py mcmanager/console/tests/test_view_logs.py mcmanager/console/tests/test_views_start_stop_e2e.py
git commit -m "feat: add correct HTTP status codes to console/views.py error responses"
```

---

### Task 2: `views_jars.py` + `views_backups.py` status codes

**Files:**
- Modify: `mcmanager/console/views_jars.py`
- Modify: `mcmanager/console/views_backups.py`
- Modify: `mcmanager/console/tests/test_views_jars.py`
- Modify: `mcmanager/console/tests/test_views_backups.py`

**Interfaces:**
- Consumes: `json_utils.json_error(message, status=400)` (Task 1).
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Update `mcmanager/console/views_jars.py`**

Add this import alongside the existing ones:

```python
from .json_utils import json_error
```

Replace `list_jar_versions`:

```python
@staff_member_required
def list_jar_versions(request, provider):
    if provider not in VALID_PROVIDERS:
        return json_error(f'Unknown provider: {provider}', status=400)
    try:
        versions = jars.list_versions(provider)
    except Exception as exc:
        return json_error(str(exc), status=502)
    return JsonResponse({
        'status': 'success',
        'versions': [{'version': v.version, 'label': v.label} for v in versions],
    })
```

Replace the `start_jar_download` validation branch:

```python
    if provider not in VALID_PROVIDERS or not version:
        return json_error('Invalid provider or version', status=400)
```

Replace `jar_download_status`'s not-found branch:

```python
    except JarDownload.DoesNotExist:
        return json_error('Download not found', status=404)
```

- [ ] **Step 2: Update `mcmanager/console/views_backups.py`**

Add this import alongside the existing ones:

```python
from .json_utils import json_error
```

Replace `backup_status_view`'s not-found branch:

```python
    except Backup.DoesNotExist:
        return json_error('Backup not found', status=404)
```

Replace `restore_backup_view`:

```python
@staff_member_required
@require_POST
def restore_backup_view(request, server_id):
    server = Server.objects.get(id=server_id)
    filename = request.POST.get('filename')
    if not filename:
        return json_error('No backup filename provided', status=400)
    try:
        backups.start_restore(server, filename)
    except Exception as exc:
        return json_error(str(exc), status=409)
    return JsonResponse({'status': 'success', 'message': 'Backup restored'})
```

Replace `delete_backup_view`:

```python
@staff_member_required
@require_POST
def delete_backup_view(request, server_id):
    server = Server.objects.get(id=server_id)
    filename = request.POST.get('filename')
    if not filename:
        return json_error('No backup filename provided', status=400)
    try:
        backups.delete_backup(server, filename)
    except Exception as exc:
        return json_error(str(exc), status=409)
    return JsonResponse({'status': 'success'})
```

- [ ] **Step 3: Update existing tests that assumed error responses were HTTP 200**

In `mcmanager/console/tests/test_views_jars.py`, change the `status_code` assertion in these 3 tests (nothing else in the file changes):

```python
@pytest.mark.django_db
def test_list_jar_versions_returns_clean_error_on_provider_failure(staff_client):
    with patch(
        "mcmanager.console.views_jars.jars.list_versions",
        side_effect=RuntimeError("network down"),
    ):
        resp = staff_client.get("/console/jars/versions/mojang")

    assert resp.status_code == 502
    body = resp.json()
    assert body["status"] == "error"


@pytest.mark.django_db
def test_start_jar_download_rejects_unknown_provider(staff_client):
    resp = staff_client.post("/console/jars/download", {"provider": "bogus", "version": "1.20.4"})
    assert resp.status_code == 400
    assert resp.json()["status"] == "error"


@pytest.mark.django_db
def test_jar_download_status_returns_error_for_unknown_id(staff_client):
    resp = staff_client.get("/console/jars/download/999999")
    assert resp.status_code == 404
    assert resp.json()["status"] == "error"
```

In `mcmanager/console/tests/test_views_backups.py`, change the `status_code` assertion in these 4 tests (nothing else in the file changes):

```python
@pytest.mark.django_db
def test_backup_status_view_returns_error_for_unknown_id(staff_client):
    resp = staff_client.get("/console/backups/status/999999")
    assert resp.status_code == 404
    assert resp.json()["status"] == "error"


@pytest.mark.django_db
def test_restore_backup_view_rejects_when_server_running(staff_client, server):
    with patch(
        "mcmanager.console.views_backups.backups.start_restore",
        side_effect=backups.RestoreServerRunningError("running"),
    ):
        resp = staff_client.post(f"/console/backups/{server.id}/restore", {"filename": "20260101T000000Z.zip"})

    assert resp.status_code == 409
    assert resp.json()["status"] == "error"


@pytest.mark.django_db
def test_restore_backup_view_handles_corrupt_archive(staff_client, server):
    with patch(
        "mcmanager.console.views_backups.backups.start_restore",
        side_effect=zipfile.BadZipFile("corrupt archive"),
    ):
        resp = staff_client.post(f"/console/backups/{server.id}/restore", {"filename": "20260101T000000Z.zip"})

    assert resp.status_code == 409
    body = resp.json()
    assert body["status"] == "error"
    assert "corrupt archive" in body["message"]


@pytest.mark.django_db
def test_delete_backup_view_handles_os_error(staff_client, server):
    with patch(
        "mcmanager.console.views_backups.backups.delete_backup",
        side_effect=OSError("disk full"),
    ):
        resp = staff_client.post(f"/console/backups/{server.id}/delete", {"filename": "20260101T000000Z.zip"})

    assert resp.status_code == 409
    body = resp.json()
    assert body["status"] == "error"
    assert "disk full" in body["message"]
```

- [ ] **Step 4: Run the affected test files**

Run: `python -m pytest mcmanager/console/tests/test_views_jars.py mcmanager/console/tests/test_views_backups.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add mcmanager/console/views_jars.py mcmanager/console/views_backups.py mcmanager/console/tests/test_views_jars.py mcmanager/console/tests/test_views_backups.py
git commit -m "feat: add correct HTTP status codes to jar and backup view error responses"
```

---

### Task 3: Port-in-use pre-check

**Files:**
- Modify: `mcmanager/console/services/process.py`
- Modify: `mcmanager/console/views.py`
- Modify: `mcmanager/console/tests/test_process.py`
- Test: new test in `test_process.py` (see below)

**Interfaces:**
- Consumes: nothing from other tasks (independent of Tasks 1/2's retrofit, though it reuses the `json_error` helper Task 1 introduced).
- Produces: `process.PortInUseError` (exception class), `process._check_port_available(port)` (raises `PortInUseError` if the port can't be bound). Task 4 does not depend on this function directly, but both tasks modify the same `start()` function body.

- [ ] **Step 1: Write the failing test**

Add to `mcmanager/console/tests/test_process.py` (add `import socket` to the top of the file alongside the existing imports):

```python
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
```

(This test does not need `@pytest.mark.django_db` beyond what the `server`/`fake_java` fixtures already require — check the existing tests in this file for the marker convention they use and match it; the file's other `def test_*(server, fake_java):` tests are all marked `@pytest.mark.django_db`, so add that marker here too.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest mcmanager/console/tests/test_process.py::test_start_raises_port_in_use_error_when_port_is_occupied -v`
Expected: FAIL with `AttributeError: module 'mcmanager.console.services.process' has no attribute 'PortInUseError'`

- [ ] **Step 3: Add the port check to `process.py`**

Add `import socket` to the top of `mcmanager/console/services/process.py`, alongside the existing `json`/`os`/`subprocess` imports.

Add this new exception class below the existing ones (after `StopTimeoutError`):

```python
class PortInUseError(Exception):
    """Raised by start() when the server's configured port is already bound
    by something else on this machine."""
```

Add this new function above `start()`:

```python
def _check_port_available(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(('0.0.0.0', port))
        except OSError as exc:
            raise PortInUseError(f'Port {port} is already in use') from exc
```

Modify `start()` to call this right after the existing `is_running` check:

```python
def start(server):
    if is_running(server):
        raise AlreadyRunningError(f'Server {server.id} is already running')

    _check_port_available(server.port)

    server_dir = settings.SERVERS_DIR / f'server_{server.id}'
```

(The rest of `start()`'s body — the `cmd` list, `kwargs`, `subprocess.Popen` call, `_write_state` — is unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest mcmanager/console/tests/test_process.py -v`
Expected: all tests pass, including the new one

- [ ] **Step 5: Wire the new exception into the `start_server` view**

In `mcmanager/console/views.py`, add one more `except` branch to `start_server` (after the existing `except process.JavaNotFoundError` branch):

```python
    except process.AlreadyRunningError:
        return json_error('Server is already running', status=409)
    except process.JavaNotFoundError as e:
        return json_error(str(e), status=503)
    except process.PortInUseError as e:
        return json_error(str(e), status=409)
```

- [ ] **Step 6: Add a view-level test**

Add to `mcmanager/console/tests/test_views_start_stop_e2e.py` (add `import socket` to the top of the file):

```python
@pytest.mark.django_db
def test_start_server_port_in_use_returns_conflict(staff_client, provisioned_server):
    server = provisioned_server
    blocking_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocking_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocking_socket.bind(('0.0.0.0', server.port))
    blocking_socket.listen(1)
    try:
        resp = staff_client.post(f"/console/start_server/{server.id}")
        assert resp.status_code == 409
        assert resp.json()["status"] == "error"
        assert process.is_running(server) is False
    finally:
        blocking_socket.close()
```

- [ ] **Step 7: Run the full test suite**

Run: `python -m pytest -v`
Expected: all tests pass (the pre-existing, documented SQLite lock-contention flake may intermittently affect unrelated tests in `test_jars.py`/`test_backups.py` — if that's the only failure, re-run just that file to confirm it's the known flake, not a regression from this task's diff).

- [ ] **Step 8: Commit**

```bash
git add mcmanager/console/services/process.py mcmanager/console/views.py mcmanager/console/tests/test_process.py mcmanager/console/tests/test_views_start_stop_e2e.py
git commit -m "feat: detect port already in use before starting a server"
```

---

### Task 4: Jar-missing pre-check + dashboard "broken" status

**Files:**
- Modify: `mcmanager/console/services/process.py`
- Modify: `mcmanager/console/services/dashboard.py`
- Modify: `mcmanager/console/views.py`
- Modify: `mcmanager/console/templates/index.html`
- Modify: `mcmanager/console/tests/test_process.py`
- Test: `mcmanager/console/tests/test_dashboard.py` (new test)

**Interfaces:**
- Consumes: nothing from Task 3 directly (both tasks modify `start()`, applied in sequence).
- Produces: `process.JarMissingError` (exception class), `process.is_jar_missing(server)` (returns `True`/`False`, never raises). `dashboard.get_dashboard_data()`'s return dicts gain a `jar_missing` key for every entry (both running and stopped servers). `_serialize_dashboard_entries` (in `views.py`) exposes this as `jar_missing` in the JSON payload consumed by the dashboard template.

- [ ] **Step 1: Write the failing tests for `process.py`**

Add to `mcmanager/console/tests/test_process.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest mcmanager/console/tests/test_process.py -v -k jar_missing`
Expected: FAIL with `AttributeError: module 'mcmanager.console.services.process' has no attribute 'is_jar_missing'`

- [ ] **Step 3: Add the jar check to `process.py`**

Add this new exception class below `PortInUseError`:

```python
class JarMissingError(Exception):
    """Raised by start() when the server's configured jar file is missing from disk."""
```

Add these two new functions above `start()` (below `_check_port_available`):

```python
def _jar_path(server):
    return settings.SERVERS_DIR / f'server_{server.id}' / server.jar


def is_jar_missing(server):
    if not server.jar:
        return True
    return not _jar_path(server).exists()
```

Modify `start()` to check this right after the port check:

```python
def start(server):
    if is_running(server):
        raise AlreadyRunningError(f'Server {server.id} is already running')

    _check_port_available(server.port)

    if is_jar_missing(server):
        raise JarMissingError(f'Jar file missing for server {server.id}: {_jar_path(server)}')

    server_dir = settings.SERVERS_DIR / f'server_{server.id}'
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest mcmanager/console/tests/test_process.py -v`
Expected: all tests pass

- [ ] **Step 5: Wire the new exception into the `start_server` view**

In `mcmanager/console/views.py`, add one more `except` branch to `start_server` (after the existing `except process.PortInUseError` branch added in Task 3):

```python
    except process.PortInUseError as e:
        return json_error(str(e), status=409)
    except process.JarMissingError as e:
        return json_error(str(e), status=409)
```

- [ ] **Step 6: Write the failing test for the dashboard field**

Add to `mcmanager/console/tests/test_dashboard.py`:

```python
@pytest.mark.django_db
def test_stopped_server_with_no_jar_reports_jar_missing_true(stopped_server):
    with patch("mcmanager.console.services.dashboard.process.is_running", return_value=False):
        result = dashboard.get_dashboard_data()

    entry = result[0]
    assert entry['jar_missing'] is True
```

(`stopped_server` is the existing fixture in this file — a bare `Server.objects.create(...)` with no `jar` set, so `is_jar_missing` should be `True` for it without any new mocking.)

- [ ] **Step 7: Run test to verify it fails**

Run: `python -m pytest mcmanager/console/tests/test_dashboard.py -v -k jar_missing`
Expected: FAIL with `KeyError: 'jar_missing'`

- [ ] **Step 8: Add `jar_missing` to the dashboard aggregation**

In `mcmanager/console/services/dashboard.py`, change the `get_dashboard_data()` return statement:

```python
    return [
        {
            'server': s,
            'running': running[s.id],
            'jar_missing': process.is_jar_missing(s),
            **(details.get(s.id) or {}),
        }
        for s in servers
    ]
```

Run: `python -m pytest mcmanager/console/tests/test_dashboard.py -v`
Expected: PASS, all 5 tests (4 existing + 1 new)

- [ ] **Step 9: Add `jar_missing` to the serialized JSON payload**

In `mcmanager/console/views.py`, update `_serialize_dashboard_entries`:

```python
def _serialize_dashboard_entries(entries):
    return [
        {
            'id': entry['server'].id,
            'name': entry['server'].name,
            'running': entry['running'],
            'jar_missing': entry.get('jar_missing', False),
            'stats_available': entry.get('stats_available', False),
            'cpu_usage': entry.get('cpu_usage'),
            'memory_usage': entry.get('memory_usage'),
            'players_available': entry.get('players_available', False),
            'players_raw': entry.get('players_raw'),
        }
        for entry in entries
    ]
```

- [ ] **Step 10: Update the dashboard template to show a "Quebrado" badge**

In `mcmanager/console/templates/index.html`, replace the start of `renderCard`:

```javascript
            function renderCard(entry) {
                const statusBadge = entry.running
                    ? '<span class="text-green-600 font-bold">ON</span>'
                    : '<span class="text-red-600 font-bold">OFF</span>';
```

with:

```javascript
            function renderCard(entry) {
                let statusBadge;
                if (entry.jar_missing) {
                    statusBadge = '<span class="text-yellow-600 font-bold">QUEBRADO</span>';
                } else if (entry.running) {
                    statusBadge = '<span class="text-green-600 font-bold">ON</span>';
                } else {
                    statusBadge = '<span class="text-red-600 font-bold">OFF</span>';
                }
```

Replace the `startBtn` line:

```javascript
                const startBtn = entry.running ? '' : `
                    <button onclick="startServer(${entry.id})"
                            class="bg-blue-500 hover:bg-blue-700 text-white font-bold py-1 px-3 rounded mr-2">
                        Start
                    </button>`;
```

with:

```javascript
                const startBtn = (entry.running || entry.jar_missing) ? '' : `
                    <button onclick="startServer(${entry.id})"
                            class="bg-blue-500 hover:bg-blue-700 text-white font-bold py-1 px-3 rounded mr-2">
                        Start
                    </button>`;
```

(Everything else in `renderCard` — `statsHtml`, `stopBtn`, the returned template literal — is unchanged.)

- [ ] **Step 11: Run the full test suite**

Run: `python -m pytest -v`
Expected: all tests pass (the pre-existing, documented SQLite lock-contention flake may intermittently affect unrelated tests in `test_jars.py`/`test_backups.py` — if that's the only failure, re-run just that file to confirm it's the known flake, not a regression from this task's diff).

- [ ] **Step 12: Manually verify in a browser**

Run the project's usual dev-server launch, open the home page with at least one provisioned server, then delete that server's jar file from disk (e.g. `SERVERS_DIR/server_<id>/<jar>`) and wait for the next dashboard poll (or reload). Confirm the card shows a yellow "QUEBRADO" badge with no Start button, and that a manual `POST` to `/console/start_server/<id>` for that server returns HTTP 409 with a clear message.

- [ ] **Step 13: Commit**

```bash
git add mcmanager/console/services/process.py mcmanager/console/services/dashboard.py mcmanager/console/views.py mcmanager/console/templates/index.html mcmanager/console/tests/test_process.py mcmanager/console/tests/test_dashboard.py
git commit -m "feat: detect missing jar file and surface it as a broken status"
```
