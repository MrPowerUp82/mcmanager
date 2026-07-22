# Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the home page's server-selection dropdown with a dashboard showing one card per server (status, CPU/RAM, players online, start/stop buttons), refreshed by polling.

**Architecture:** A new aggregation service (`services/dashboard.py`) collects per-server status/stats/player-count, parallelizing the collection for running servers so one slow/unreachable server never delays the others. A new JSON endpoint (`dashboard_data`) exposes this for polling; the `home` view renders the initial page. The template replaces the current dropdown with cards and polls the JSON endpoint every 5 seconds.

**Tech Stack:** Django views/URLs, `concurrent.futures.ThreadPoolExecutor` (stdlib, no new dependency), `psutil` (already a dependency, via the existing `process` service), vanilla JS `fetch`/`setInterval` (same pattern already used in `mcmanager/console/templates/console/index.html`), Tailwind CSS (already the project's styling, via `mcmanager/console/static/css/tailwind.css`), Toastify (already loaded elsewhere for notifications).

## Global Constraints

- Dashboard polling interval: exactly **5000ms** (`setInterval(..., 5000)`).
- Failure isolation: a failure collecting stats or player count for one server must never prevent other servers' data from being returned, and must never crash the aggregation or the endpoint — isolate with `try/except Exception` per server, per data type.
- Stopped servers must trigger zero CPU/RCON calls — no thread spawned for them, no stats/player fields attempted.
- `process.get_stats()`'s existing behavior (used today by the individual console page's `get_server_stats` endpoint) must not change for existing callers — the new `cpu_interval` parameter must default to `1` (the current hardcoded value), so no other call site needs to change.
- The dashboard's own stats collection must pass `cpu_interval=0.2` explicitly (not the default) — this is what bounds the aggregate response time for multiple servers.
- Player-count parsing (turning the raw RCON `list` response string into a displayable count) happens in the frontend template/JS, not in `services/dashboard.py` — the service only passes through the raw RCON response text.
- Card start/stop buttons must reuse the existing `start_server`/`stop_server` endpoints (`mcmanager/console/urls.py`, names `start_server`/`stop_server`) — do not create new start/stop endpoints.
- Every card must link to `/console/<id>/` (the `index` view, existing) for the full console.
- No application code outside `services/dashboard.py`, `services/process.py` (the `cpu_interval` parameter only), `views.py`, `urls.py` (both the top-level `mcmanager/urls.py` — unchanged, it already just imports `home` — and `mcmanager/console/urls.py`), and `mcmanager/console/templates/index.html` is in scope.

---

### Task 1: Add a `cpu_interval` parameter to `process.get_stats()`

**Files:**
- Modify: `mcmanager/console/services/process.py:132-150`
- Test: `mcmanager/console/tests/test_process.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `process.get_stats(server, cpu_interval=1)` — Task 2's `services/dashboard.py` calls this with `cpu_interval=0.2`.

- [ ] **Step 1: Write the failing test**

Add to `mcmanager/console/tests/test_process.py` (near the existing `test_get_stats_returns_cpu_and_memory` test, same file, same fixtures already defined there — `server`, `fake_java`):

```python
@pytest.mark.django_db
def test_get_stats_passes_custom_cpu_interval_to_psutil(server, fake_java):
    process.start(server)
    try:
        with patch("mcmanager.console.services.process.psutil.Process") as mock_process_cls:
            mock_instance = mock_process_cls.return_value
            mock_instance.cpu_percent.return_value = 12.5
            mock_instance.memory_info.return_value.rss = 1024 * 1024 * 50
            stats = process.get_stats(server, cpu_interval=0.2)
            mock_instance.cpu_percent.assert_called_once_with(interval=0.2)
            assert stats["cpu_usage"] == 12.5
    finally:
        process.force_stop(server)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest mcmanager/console/tests/test_process.py::test_get_stats_passes_custom_cpu_interval_to_psutil -v`
Expected: FAIL — `TypeError: get_stats() got an unexpected keyword argument 'cpu_interval'`

- [ ] **Step 3: Add the parameter**

In `mcmanager/console/services/process.py`, change:

```python
def get_stats(server):
    state = _read_state(server)
    if state is None or not is_running(server):
        raise ProcessNotRunningError(f'Server {server.id} is not running')
    try:
        process_handle = psutil.Process(state['pid'])
        cpu_usage = process_handle.cpu_percent(interval=1)
```

to:

```python
def get_stats(server, cpu_interval=1):
    state = _read_state(server)
    if state is None or not is_running(server):
        raise ProcessNotRunningError(f'Server {server.id} is not running')
    try:
        process_handle = psutil.Process(state['pid'])
        cpu_usage = process_handle.cpu_percent(interval=cpu_interval)
```

(The rest of the function body is unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest mcmanager/console/tests/test_process.py::test_get_stats_passes_custom_cpu_interval_to_psutil -v`
Expected: PASS

- [ ] **Step 5: Run the full existing test_process.py file to confirm no regression**

Run: `python -m pytest mcmanager/console/tests/test_process.py -v`
Expected: all tests pass, including the pre-existing `test_get_stats_returns_cpu_and_memory` (which calls `get_stats(server)` with no `cpu_interval`, exercising the new default).

- [ ] **Step 6: Commit**

```bash
git add mcmanager/console/services/process.py mcmanager/console/tests/test_process.py
git commit -m "feat: add cpu_interval parameter to process.get_stats"
```

---

### Task 2: `services/dashboard.py` — parallel per-server data aggregation

**Files:**
- Create: `mcmanager/console/services/dashboard.py`
- Test: `mcmanager/console/tests/test_dashboard.py`

**Interfaces:**
- Consumes: `process.is_running(server)`, `process.get_stats(server, cpu_interval=0.2)` (Task 1), `process.send_command(server, 'list')`, `process.ProcessNotRunningError` — all from `mcmanager.console.services.process`, already existing except the `cpu_interval` parameter added in Task 1.
- Produces: `dashboard.get_dashboard_data()` — takes no arguments, returns a `list` with one `dict` per `Server` (in `Server.objects.all()` order), each dict shaped:
  ```python
  {
      'server': <Server instance>,
      'running': <bool>,
      'stats_available': <bool>,       # only present-and-True if running and stats collection succeeded
      'cpu_usage': <float or absent>,
      'memory_usage': <float or absent>,
      'players_available': <bool>,     # only present-and-True if running and RCON succeeded
      'players_raw': <str or absent>,  # raw RCON 'list' response text
  }
  ```
  Task 3's views consume this directly.

- [ ] **Step 1: Write the failing tests**

Create `mcmanager/console/tests/test_dashboard.py`:

```python
from unittest.mock import patch

import pytest

from mcmanager.console.models import Server, Type
from mcmanager.console.services import dashboard, process


@pytest.fixture
def server_type(db):
    return Type.objects.create(name="Vanilla")


@pytest.fixture
def stopped_server(server_type):
    return Server.objects.create(name="Stopped", jar_template="paper.jar", port=25570, type=server_type)


@pytest.fixture
def running_server(server_type):
    return Server.objects.create(name="Running", jar_template="paper.jar", port=25571, type=server_type)


@pytest.mark.django_db
def test_stopped_server_has_no_stats_or_players_and_is_not_polled(stopped_server):
    with patch("mcmanager.console.services.dashboard.process.is_running", return_value=False), \
         patch("mcmanager.console.services.dashboard.process.get_stats") as mock_get_stats, \
         patch("mcmanager.console.services.dashboard.process.send_command") as mock_send_command:
        result = dashboard.get_dashboard_data()

    assert len(result) == 1
    entry = result[0]
    assert entry['server'] == stopped_server
    assert entry['running'] is False
    assert entry.get('stats_available', False) is False
    assert entry.get('players_available', False) is False
    mock_get_stats.assert_not_called()
    mock_send_command.assert_not_called()


@pytest.mark.django_db
def test_running_server_with_working_stats_and_rcon_reports_both(running_server):
    with patch("mcmanager.console.services.dashboard.process.is_running", return_value=True), \
         patch(
             "mcmanager.console.services.dashboard.process.get_stats",
             return_value={'cpu_usage': 12.5, 'memory_usage': 256.0, 'total_memory': 1024.0,
                           'used_memory': 512.0, 'total_cpu_usage': 30.0},
         ) as mock_get_stats, \
         patch(
             "mcmanager.console.services.dashboard.process.send_command",
             return_value='There are 1 of a max of 20 players online: Steve',
         ):
        result = dashboard.get_dashboard_data()

    entry = result[0]
    assert entry['running'] is True
    assert entry['stats_available'] is True
    assert entry['cpu_usage'] == 12.5
    assert entry['memory_usage'] == 256.0
    assert entry['players_available'] is True
    assert entry['players_raw'] == 'There are 1 of a max of 20 players online: Steve'
    mock_get_stats.assert_called_once_with(running_server, cpu_interval=dashboard.STATS_CPU_INTERVAL)


@pytest.mark.django_db
def test_rcon_failure_marks_only_players_unavailable_stats_still_reported(running_server):
    with patch("mcmanager.console.services.dashboard.process.is_running", return_value=True), \
         patch(
             "mcmanager.console.services.dashboard.process.get_stats",
             return_value={'cpu_usage': 5.0, 'memory_usage': 100.0, 'total_memory': 1024.0,
                           'used_memory': 512.0, 'total_cpu_usage': 10.0},
         ), \
         patch(
             "mcmanager.console.services.dashboard.process.send_command",
             side_effect=Exception("RCON connection refused"),
         ):
        result = dashboard.get_dashboard_data()

    entry = result[0]
    assert entry['stats_available'] is True
    assert entry['cpu_usage'] == 5.0
    assert entry.get('players_available', False) is False


@pytest.mark.django_db
def test_stats_failure_for_one_server_does_not_prevent_other_servers_reporting(server_type):
    broken = Server.objects.create(name="Broken", jar_template="paper.jar", port=25572, type=server_type)
    healthy = Server.objects.create(name="Healthy", jar_template="paper.jar", port=25573, type=server_type)

    def fake_get_stats(server, cpu_interval):
        if server.id == broken.id:
            raise process.ProcessNotRunningError("race: process exited")
        return {'cpu_usage': 1.0, 'memory_usage': 10.0, 'total_memory': 1024.0,
                'used_memory': 512.0, 'total_cpu_usage': 2.0}

    with patch("mcmanager.console.services.dashboard.process.is_running", return_value=True), \
         patch("mcmanager.console.services.dashboard.process.get_stats", side_effect=fake_get_stats), \
         patch("mcmanager.console.services.dashboard.process.send_command", return_value='There are 0 of a max of 20 players online: '):
        result = dashboard.get_dashboard_data()

    by_id = {entry['server'].id: entry for entry in result}
    assert by_id[broken.id].get('stats_available', False) is False
    assert by_id[healthy.id]['stats_available'] is True
    assert by_id[healthy.id]['cpu_usage'] == 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest mcmanager/console/tests/test_dashboard.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcmanager.console.services.dashboard'`

- [ ] **Step 3: Create `mcmanager/console/services/dashboard.py`**

```python
"""Aggregates per-server status/stats/player-count for the dashboard view.
Each running server's data is collected in its own thread so one slow or
unreachable server doesn't add its latency to every other server's; a
failure for one server never prevents the others from reporting."""
from concurrent.futures import ThreadPoolExecutor

from ..models import Server
from . import process

STATS_CPU_INTERVAL = 0.2


def get_dashboard_data():
    servers = list(Server.objects.all())
    running = {s.id: process.is_running(s) for s in servers}
    running_servers = [s for s in servers if running[s.id]]

    details = {}
    if running_servers:
        with ThreadPoolExecutor(max_workers=len(running_servers)) as pool:
            collected = pool.map(_collect_running_server_data, running_servers)
        details = dict(zip((s.id for s in running_servers), collected))

    return [
        {
            'server': s,
            'running': running[s.id],
            **(details.get(s.id) or {}),
        }
        for s in servers
    ]


def _collect_running_server_data(server):
    result = {'stats_available': False, 'players_available': False}
    try:
        stats = process.get_stats(server, cpu_interval=STATS_CPU_INTERVAL)
        result['cpu_usage'] = stats['cpu_usage']
        result['memory_usage'] = stats['memory_usage']
        result['stats_available'] = True
    except Exception:
        pass
    try:
        result['players_raw'] = process.send_command(server, 'list')
        result['players_available'] = True
    except Exception:
        pass
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest mcmanager/console/tests/test_dashboard.py -v`
Expected: PASS, all 4 tests

- [ ] **Step 5: Commit**

```bash
git add mcmanager/console/services/dashboard.py mcmanager/console/tests/test_dashboard.py
git commit -m "feat: add dashboard service for parallel per-server status/stats/player aggregation"
```

---

### Task 3: Views, URLs, and dashboard template

**Files:**
- Modify: `mcmanager/console/views.py:119-124` (the `home` view)
- Modify: `mcmanager/console/urls.py`
- Modify: `mcmanager/console/templates/index.html`
- Modify: `mcmanager/console/tests/test_home_view.py` (existing dropdown-based assertions no longer apply)
- Test: `mcmanager/console/tests/test_dashboard_views.py` (new, for the `dashboard_data` JSON endpoint)

**Interfaces:**
- Consumes: `dashboard.get_dashboard_data()` (Task 2) — exact return shape documented in Task 2's Interfaces block.
- Produces: nothing consumed by later tasks (this is the final task in this plan).

- [ ] **Step 1: Write the failing tests for the new `dashboard_data` endpoint**

Create `mcmanager/console/tests/test_dashboard_views.py`:

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
def test_dashboard_data_requires_staff_login(client, server):
    response = client.get(reverse("dashboard_data"))
    assert response.status_code in (302, 403)


@pytest.mark.django_db
def test_dashboard_data_returns_server_list_json(staff_client, server):
    with patch(
        "mcmanager.console.views.dashboard.get_dashboard_data",
        return_value=[{
            'server': server,
            'running': True,
            'stats_available': True,
            'cpu_usage': 7.5,
            'memory_usage': 128.0,
            'players_available': True,
            'players_raw': 'There are 0 of a max of 20 players online: ',
        }],
    ):
        response = staff_client.get(reverse("dashboard_data"))

    assert response.status_code == 200
    data = response.json()
    assert data['status'] == 'success'
    assert len(data['servers']) == 1
    entry = data['servers'][0]
    assert entry['id'] == server.id
    assert entry['name'] == server.name
    assert entry['running'] is True
    assert entry['stats_available'] is True
    assert entry['cpu_usage'] == 7.5
    assert entry['memory_usage'] == 128.0
    assert entry['players_available'] is True
    assert entry['players_raw'] == 'There are 0 of a max of 20 players online: '


@pytest.mark.django_db
def test_dashboard_data_handles_stopped_server_with_no_stats_fields(staff_client, server):
    with patch(
        "mcmanager.console.views.dashboard.get_dashboard_data",
        return_value=[{'server': server, 'running': False}],
    ):
        response = staff_client.get(reverse("dashboard_data"))

    assert response.status_code == 200
    entry = response.json()['servers'][0]
    assert entry['running'] is False
    assert entry['stats_available'] is False
    assert entry['players_available'] is False
    assert entry['cpu_usage'] is None
    assert entry['players_raw'] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest mcmanager/console/tests/test_dashboard_views.py -v`
Expected: FAIL — `NoReverseMatch: Reverse for 'dashboard_data' not found`

- [ ] **Step 3: Update the existing `test_home_view.py` for the new card-based rendering**

The current `home` view renders `(ON)`/`(OFF)` text next to a dropdown option; the new version renders cards fed by `dashboard.get_dashboard_data()`. Replace the two rendering tests (keep the third, `test_server_model_has_no_status_field`, unchanged) in `mcmanager/console/tests/test_home_view.py`:

```python
from unittest.mock import patch

import pytest
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
def test_home_renders_a_card_per_server(client, server, staff_user):
    client.force_login(staff_user)
    with patch(
        "mcmanager.console.views.dashboard.get_dashboard_data",
        return_value=[{'server': server, 'running': False}],
    ):
        response = client.get(reverse("home"))
    assert response.status_code == 200
    assert server.name in response.content.decode()


def test_server_model_has_no_status_field():
    field_names = {f.name for f in Server._meta.get_fields()}
    assert "status" not in field_names
```

- [ ] **Step 4: Update `views.py`**

In `mcmanager/console/views.py`, change the import line:

```python
from .services import process, rcon
```

to:

```python
from .services import dashboard, process, rcon
```

`redirect` (imported via `from django.shortcuts import redirect, render`) is used ONLY inside the `home` view being replaced in this step (confirmed via `grep -n "redirect(" mcmanager/console/views.py` returning exactly one match, inside `home`) — after this step, no view calls `redirect(...)` anymore, so change that import line too:

```python
from django.shortcuts import redirect, render
```

to:

```python
from django.shortcuts import render
```

Then replace the `home` view (currently lines 119-124):

```python
@staff_member_required
def home(request: HttpRequest):
    if request.method == 'POST':
        server_id = request.POST.get('server_id')
        return redirect('index', id=server_id)
    ctx = {"servers": [(s, process.is_running(s)) for s in Server.objects.all()]}
    return render(request, 'index.html', ctx)
```

with:

```python
@staff_member_required
def home(request: HttpRequest):
    return render(request, 'index.html', {})


@staff_member_required
def dashboard_data(request: HttpRequest):
    entries = dashboard.get_dashboard_data()
    return JsonResponse({'status': 'success', 'servers': [
        {
            'id': entry['server'].id,
            'name': entry['server'].name,
            'running': entry['running'],
            'stats_available': entry.get('stats_available', False),
            'cpu_usage': entry.get('cpu_usage'),
            'memory_usage': entry.get('memory_usage'),
            'players_available': entry.get('players_available', False),
            'players_raw': entry.get('players_raw'),
        }
        for entry in entries
    ]})
```

`redirect` may now be unused in `views.py` if no other view uses it — check with `grep -n "redirect(" mcmanager/console/views.py` before removing the import; leave the import alone if any other view still calls `redirect(...)`.

- [ ] **Step 5: Add the `dashboard_data` URL**

In `mcmanager/console/urls.py`, add one line to `urlpatterns` (anywhere in the list; placing it near the top, right after the `path('<int:id>', ...)` line, is fine):

```python
path('dashboard/data/', views.dashboard_data, name='dashboard_data'),
```

- [ ] **Step 6: Run the view/URL tests to verify they pass**

Run: `python -m pytest mcmanager/console/tests/test_dashboard_views.py mcmanager/console/tests/test_home_view.py -v`
Expected: PASS, all tests

- [ ] **Step 7: Rewrite the dashboard template**

Replace the full contents of `mcmanager/console/templates/index.html` with:

```html
{% load static %}
<!DOCTYPE html>
<html lang="pt-br">
    <head>
        <meta charset="UTF-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1.0" />
        <title>Minecraft Console</title>
        <link rel="icon" type="image/x-icon" href="{% static 'img/favicon.ico' %}" />
        <link href="{% static 'css/tailwind.css' %}" rel="stylesheet" />
        <link href="{% static 'css/toastify.min.css' %}" rel="stylesheet" />
    </head>
    <body class="bg-gray-100 p-6">
        <div class="container mx-auto">
            <h1 class="text-3xl font-bold mb-4">Minecraft Servers</h1>
            <form>{% csrf_token %}</form>
            <a href="{% url 'jars_page' %}" class="text-blue-500 hover:underline mb-4 inline-block">
                Gerenciar jars
            </a>
            <div id="dashboard-cards" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4"></div>
        </div>
        <script type="text/javascript" src="{% static 'js/toastify-js.js' %}"></script>
        <script>
            function getCsrfToken() {
                return document.querySelector('[name=csrfmiddlewaretoken]').value;
            }

            function postJSON(url) {
                return fetch(url, {
                    method: 'POST',
                    headers: { 'X-CSRFToken': getCsrfToken() }
                }).then(response => response.json());
            }

            function create_toast(message, bgColor, textColor) {
                Toastify({
                    text: message,
                    duration: 4000,
                    newWindow: true,
                    close: true,
                    gravity: "top",
                    position: "right",
                    stopOnFocus: true,
                    style: { background: bgColor, color: textColor },
                }).showToast();
            }

            function parsePlayers(playersRaw) {
                if (!playersRaw) {
                    return null;
                }
                const match = playersRaw.match(/There are (\d+) of a max of (\d+) players online/);
                if (!match) {
                    return null;
                }
                return `${match[1]}/${match[2]}`;
            }

            function renderCard(entry) {
                const statusBadge = entry.running
                    ? '<span class="text-green-600 font-bold">ON</span>'
                    : '<span class="text-red-600 font-bold">OFF</span>';

                let statsHtml = '';
                if (entry.running) {
                    const cpuText = entry.stats_available ? `${entry.cpu_usage.toFixed(1)}%` : 'indisponível';
                    const memText = entry.stats_available ? `${entry.memory_usage.toFixed(0)} MB` : 'indisponível';
                    const playersText = entry.players_available ? (parsePlayers(entry.players_raw) || 'indisponível') : 'indisponível';
                    statsHtml = `
                        <p><strong>CPU:</strong> ${cpuText}</p>
                        <p><strong>RAM:</strong> ${memText}</p>
                        <p><strong>Jogadores:</strong> ${playersText}</p>
                    `;
                }

                const startBtn = entry.running ? '' : `
                    <button onclick="startServer(${entry.id})"
                            class="bg-blue-500 hover:bg-blue-700 text-white font-bold py-1 px-3 rounded mr-2">
                        Start
                    </button>`;
                const stopBtn = entry.running ? `
                    <button onclick="stopServer(${entry.id})"
                            class="bg-red-500 hover:bg-red-700 text-white font-bold py-1 px-3 rounded mr-2">
                        Stop
                    </button>` : '';

                return `
                    <div class="bg-white p-4 border rounded shadow-md" id="card-${entry.id}">
                        <h2 class="text-xl font-bold mb-2">${entry.name} ${statusBadge}</h2>
                        ${statsHtml}
                        <div class="mt-4">
                            ${startBtn}
                            ${stopBtn}
                            <a href="/console/${entry.id}"
                               class="bg-gray-500 hover:bg-gray-700 text-white font-bold py-1 px-3 rounded inline-block">
                                Abrir console
                            </a>
                        </div>
                    </div>
                `;
            }

            function fetchDashboardData() {
                fetch("{% url 'dashboard_data' %}")
                    .then(response => response.json())
                    .then(data => {
                        if (data.status !== 'success') {
                            return;
                        }
                        const container = document.getElementById('dashboard-cards');
                        container.innerHTML = data.servers.map(renderCard).join('');
                    });
            }

            function startServer(id) {
                postJSON(`/console/start_server/${id}`)
                    .then(data => {
                        create_toast(data.message, 'green', 'white');
                        fetchDashboardData();
                    });
            }

            function stopServer(id) {
                postJSON(`/console/stop_server/${id}`)
                    .then(data => {
                        create_toast(data.message, 'red', 'white');
                        fetchDashboardData();
                    });
            }

            fetchDashboardData();
            setInterval(fetchDashboardData, 5000);
        </script>
    </body>
</html>
```

- [ ] **Step 8: Run the full test suite**

Run: `python -m pytest -v`
Expected: all tests pass (no failures introduced by this task; the pre-existing, documented SQLite lock-contention flake may intermittently affect unrelated tests in `test_jars.py`/`test_backups.py` — if that's the only failure, re-run just that file to confirm it's the known flake, not a regression from this task's diff).

- [ ] **Step 9: Manually verify in a browser**

Run: `python manage.py runserver` (or the project's usual dev-server launch command), then open the home page (`/`) in a browser with at least one server configured. Confirm: cards render, a stopped server shows a Start button and no stats, starting a server updates its card without a page reload within ~5 seconds (or immediately after the click), and "Abrir console" navigates to that server's console page.

- [ ] **Step 10: Commit**

```bash
git add mcmanager/console/views.py mcmanager/console/urls.py mcmanager/console/templates/index.html mcmanager/console/tests/test_dashboard_views.py mcmanager/console/tests/test_home_view.py
git commit -m "feat: replace home dropdown with a polling dashboard of per-server cards"
```
