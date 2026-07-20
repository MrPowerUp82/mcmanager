# Real-Time Console (Incremental Log Polling) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the server console's log view incremental — `view_logs` returns only new bytes since the client's last-seen offset instead of re-sending the whole file on every 2.5s poll.

**Architecture:** `view_logs` gains a `?offset=N` (bytes) query parameter, seeks the log file to that position, and returns `{status, logs, offset}` where `offset` is the new read position. The template's `viewLogs()` JS tracks the last offset in a page-level variable, appends new content instead of replacing it, and detects log truncation (a Minecraft restart recreates `latest.log`) by comparing the returned offset to what it had.

**Tech Stack:** Django (existing), vanilla JS (existing template, no new libraries).

## Global Constraints

- No new dependencies (stdlib file I/O only).
- `view_logs` keeps its existing `@staff_member_required` decorator, GET method, and URL name (`/console/view_logs/<id>`, name `view_logs`) — this is a contract change (response gains `offset`), not a route change.
- Offset semantics are **bytes**, not lines.
- If the requested offset exceeds the current file size (log truncated/recreated by a Minecraft restart), the endpoint silently restarts from byte 0 instead of erroring.
- No limit on the initial (`offset=0`) response size — YAGNI at this project's 1–10-servers-per-machine scale.
- UTF-8 decoding uses `errors='replace'` — no byte-boundary alignment logic.
- `setInterval(viewLogs, 2500)` (already in the template) is unchanged — this plan doesn't touch polling cadence.

---

### Task 1: Incremental view_logs endpoint

**Files:**
- Modify: `mcmanager/console/views.py:56-63` (the `view_logs` function)
- Test: `mcmanager/console/tests/test_view_logs.py` (create)

**Interfaces:**
- Consumes: `settings.SERVERS_DIR`, `LOG_FILE` constant (`mcmanager/console/views.py:10`, unchanged), `Server` model (unchanged).
- Produces: `view_logs(request, id)` view returning `JsonResponse({'status': 'success', 'logs': str, 'offset': int})` on success, or `JsonResponse({'status': 'error', 'message': str})` if the log file doesn't exist. `offset` in the response is always `offset_read_from + bytes_returned`. This is the last task touching this view — Task 2 only touches the template.

- [ ] **Step 1: Write the failing tests**

Create `mcmanager/console/tests/test_view_logs.py`:

```python
import pytest

from mcmanager.console.models import Server, Type


@pytest.fixture
def staff_client(client, django_user_model):
    staff = django_user_model.objects.create_user(username="admin", password="pw", is_staff=True)
    client.force_login(staff)
    return client


@pytest.fixture
def server_with_log(settings, tmp_path):
    settings.SERVERS_DIR = tmp_path / "servers"
    settings.SERVERS_DIR.mkdir()
    server_type = Type.objects.create(name="Vanilla")
    server = Server.objects.create(name="Test", jar_template="paper.jar", jar="1_paper.jar", port=25566, type=server_type)
    log_dir = settings.SERVERS_DIR / f"server_{server.id}" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "latest.log").write_text("line one\nline two\n", encoding="utf-8")
    return server


@pytest.mark.django_db
def test_view_logs_without_offset_returns_whole_file(staff_client, server_with_log):
    resp = staff_client.get(f"/console/view_logs/{server_with_log.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["logs"] == "line one\nline two\n"
    assert body["offset"] == len("line one\nline two\n".encode("utf-8"))


@pytest.mark.django_db
def test_view_logs_with_offset_returns_only_new_content(settings, staff_client, server_with_log):
    first_line_bytes = len("line one\n".encode("utf-8"))

    resp = staff_client.get(f"/console/view_logs/{server_with_log.id}?offset={first_line_bytes}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["logs"] == "line two\n"
    assert body["offset"] == len("line one\nline two\n".encode("utf-8"))


@pytest.mark.django_db
def test_view_logs_offset_past_end_of_truncated_file_restarts_from_zero(settings, staff_client, server_with_log):
    log_path = settings.SERVERS_DIR / f"server_{server_with_log.id}" / "logs" / "latest.log"
    huge_offset = 999999

    resp = staff_client.get(f"/console/view_logs/{server_with_log.id}?offset={huge_offset}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["logs"] == "line one\nline two\n"
    assert body["offset"] == log_path.stat().st_size


@pytest.mark.django_db
def test_view_logs_invalid_offset_falls_back_to_zero(staff_client, server_with_log):
    resp = staff_client.get(f"/console/view_logs/{server_with_log.id}?offset=not-a-number")

    assert resp.status_code == 200
    body = resp.json()
    assert body["logs"] == "line one\nline two\n"


@pytest.mark.django_db
def test_view_logs_negative_offset_falls_back_to_zero(staff_client, server_with_log):
    resp = staff_client.get(f"/console/view_logs/{server_with_log.id}?offset=-5")

    assert resp.status_code == 200
    body = resp.json()
    assert body["logs"] == "line one\nline two\n"


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


@pytest.mark.django_db
def test_view_logs_requires_staff_login(client, server_with_log):
    resp = client.get(f"/console/view_logs/{server_with_log.id}")
    assert resp.status_code == 302
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest mcmanager/console/tests/test_view_logs.py -v`
Expected: FAIL — `test_view_logs_without_offset_returns_whole_file` and `test_view_logs_with_offset_returns_only_new_content` fail on `assert body["offset"] == ...` (KeyError or `None == int`, since the current view doesn't return an `offset` key). `test_view_logs_with_offset_returns_only_new_content` also fails because the current view ignores the `offset` query param entirely and returns the whole file. `test_view_logs_offset_past_end_of_truncated_file_restarts_from_zero` fails for the same reason (ignores offset).

- [ ] **Step 3: Implement the incremental view_logs**

Replace `mcmanager/console/views.py:56-63` (the current `view_logs` function) with:

```python
@staff_member_required
def view_logs(request, id):
    server = Server.objects.get(id=id)
    log_path = settings.SERVERS_DIR / f'server_{server.id}' / LOG_FILE
    try:
        offset = int(request.GET.get('offset', 0))
    except ValueError:
        offset = 0
    if offset < 0:
        offset = 0

    if not log_path.exists():
        return JsonResponse({'status': 'error', 'message': 'Log file not found'})

    file_size = log_path.stat().st_size
    if offset > file_size:
        offset = 0

    with log_path.open('rb') as f:
        f.seek(offset)
        new_bytes = f.read()

    return JsonResponse({
        'status': 'success',
        'logs': new_bytes.decode('utf-8', errors='replace'),
        'offset': offset + len(new_bytes),
    })
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest mcmanager/console/tests/test_view_logs.py -v`
Expected: `7 passed`

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `python -m pytest -v`
Expected: all tests pass (existing suite plus the 7 new ones).

- [ ] **Step 6: Commit**

```bash
git add mcmanager/console/views.py mcmanager/console/tests/test_view_logs.py
git commit -m "feat: make view_logs return only new log content since a byte offset"
```

---

### Task 2: Frontend incremental polling and append

**Files:**
- Modify: `mcmanager/console/templates/console/index.html:189-200` (the `viewLogs()` function)

**Interfaces:**
- Consumes: `view_logs`'s new response shape from Task 1 (`{status, logs, offset}`).
- Produces: no new interfaces — this is the last task in this plan. `viewLogs()` keeps being called by the existing `setInterval(viewLogs, 2500)` at line 72 and the existing `onclick="viewLogs()"` button at line 41 — neither of those call sites changes.

- [ ] **Step 1: Update the template**

In `mcmanager/console/templates/console/index.html`, add a page-level offset variable right after the existing `const memory_limit = ...` line (currently line 74):

```javascript
            const memory_limit = parseFloat("{{server.memory_limit}}");
            let logOffset = 0;
```

Then replace the `viewLogs()` function (currently lines 189-200):

```javascript
            function viewLogs() {
                fetch(`{% url 'view_logs' id=server.id %}?offset=${logOffset}`)
                    .then(response => response.json())
                    .then(data => {
                        if (data.status === 'success') {
                            const logsEl = document.getElementById('logs');
                            if (data.offset < logOffset) {
                                logsEl.textContent = '';
                            }
                            logsEl.textContent += data.logs;
                            logOffset = data.offset;
                            logsEl.scrollTop = logsEl.scrollHeight;
                        } else {
                            create_toast(data.message, 'red', 'white');
                        }
                    });
            }
```

This is a pure template change — no automated test coverage exists for template JS in this codebase (confirmed: no JS test runner is configured anywhere in the project), matching the existing pattern for all prior template changes in Phase 1 and Phase 2.

- [ ] **Step 2: Manually verify in a browser**

This step touches UI behavior the automated suite doesn't cover. On a machine with Java available (or reusing the fake Java binary approach isn't practical for a real manual check — use a real or dummy jar that at least writes to `logs/latest.log`):

```bash
python manage.py createsuperuser
python manage.py runserver
```

Log in, open a server's console page, start the server (or manually append lines to its `logs/latest.log` file while the page is open to simulate server output), and confirm: the log panel only grows (doesn't flicker/reset every 2.5s), new lines appear appended to the bottom, and the panel auto-scrolls to show them. Then manually truncate/delete-and-recreate `logs/latest.log` (simulating a Minecraft restart) and confirm the log panel clears and restarts cleanly on the next poll instead of showing an error or duplicated content.

- [ ] **Step 3: Commit**

```bash
git add mcmanager/console/templates/console/index.html
git commit -m "feat: poll and append incremental log content in the console view"
```

---

## Self-review notes

- **Spec coverage:** Task 1 covers the spec's Section "Backend" in full (byte offset, truncation reset, invalid/negative offset fallback, missing file, auth). Task 2 covers "Frontend" in full (offset tracking, append not replace, truncation detection via offset comparison, auto-scroll). The spec's "no limit on initial load" and "errors='replace' without UTF-8 alignment" decisions are directly reflected in Task 1's implementation (no truncation of `new_bytes`, plain `.decode('utf-8', errors='replace')`).
- **Placeholder scan:** none found — every step has complete, exact code.
- **Type consistency:** `view_logs`'s response shape (`{status, logs, offset}`) is used identically in both tasks' code blocks. `test_view_logs_missing_file_returns_error` uses its own `tmp_path`-backed `settings.SERVERS_DIR`, independent of any other test's fixture state.
