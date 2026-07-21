# Jar Downloads Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a staff user download a Minecraft server jar (Vanilla via Mojang, or Paper via PaperMC) directly from the panel instead of placing the file manually in the jar directory.

**Architecture:** A `JarDownload` model tracks one download job (provider, version, status). Two provider modules (`jar_providers/mojang.py`, `jar_providers/paper.py`) share a tiny common interface (`list_versions()` / `get_download_info()`) and are orchestrated by `services/jars.py`, which runs the actual download in a background daemon thread, verifies the checksum the provider's API reported, and only makes the file visible to the rest of the app (via an atomic rename) once it's verified. A new `views_jars.py` + `templates/console/jars.html` expose this as a dedicated page with provider/version dropdowns and status polling.

**Tech Stack:** Django (existing), stdlib `urllib.request`/`hashlib`/`threading` — no new runtime dependencies.

## Global Constraints

- No new runtime dependencies — HTTP calls use `urllib.request` (stdlib), not `requests`.
- Every new view requires `@staff_member_required`.
- `JarDownload` has no `ForeignKey` to `Server` — it only tracks the download job, not a permanent jar registry. The existing `get_jar_files()` (`mcmanager/console/models.py:9-13`) stays the single source of truth for "what jars exist," unchanged.
- Mojang: filter to `type=='release'` only, most recent first. Hash algorithm: sha1.
- PaperMC: always resolve to the highest (most recent) build number for the chosen Minecraft version. Hash algorithm: sha256.
- On a checksum mismatch, delete the downloaded file and mark the job `error` — never leave a partial or unverified file where `get_jar_files()` could pick it up.
- Downloaded files are written to a `.part` sibling path first, then atomically `rename()`d to the final `.jar` name only after the checksum passes.
- All network calls use a 10-second timeout (version listing / metadata) except the actual jar download stream, which uses 30 seconds.
- This project's SQLite/single-process deployment model (established in Phase 2) means a plain `threading.Thread(daemon=True)` is an accepted pattern for background work — no Celery/task queue.

---

## File Structure

```
mcmanager/console/
├── models.py                       # + JarDownload
├── services/
│   ├── jars.py                      # Create: orchestrator
│   └── jar_providers/
│       ├── __init__.py               # Create: empty
│       ├── base.py                    # Create: VersionInfo, DownloadInfo
│       ├── mojang.py                  # Create
│       └── paper.py                   # Create
├── views_jars.py                    # Create: jars page + 3 JSON endpoints
├── urls.py                          # Modify: + 4 routes
├── templates/console/
│   └── jars.html                     # Create
├── templates/index.html              # Modify: add a link to the new page
├── migrations/
│   └── 0008_jardownload.py           # Create
└── tests/
    ├── test_models.py                # Modify: + JarDownload tests
    ├── test_jar_providers.py         # Create
    ├── test_jars.py                  # Create
    └── test_views_jars.py            # Create
```

---

### Task 1: JarDownload model and migration

**Files:**
- Modify: `mcmanager/console/models.py` (append after the `Server` class, which currently ends at line 75)
- Create: `mcmanager/console/migrations/0008_jardownload.py`
- Modify: `mcmanager/console/tests/test_models.py` (append)

**Interfaces:**
- Consumes: nothing new.
- Produces: `mcmanager.console.models.JarDownload` with fields `provider` (str), `version` (str), `filename` (str, blank-ok), `status` (str, one of `'pending'|'downloading'|'done'|'error'`), `error_message` (str, blank-ok), `created_at`, `updated_at`. Tasks 2-4 create/read/update instances of this model by primary key.

- [ ] **Step 1: Write the failing test**

Append to `mcmanager/console/tests/test_models.py`:

```python
from mcmanager.console.models import JarDownload


@pytest.mark.django_db
def test_jar_download_defaults_to_pending_status():
    download = JarDownload.objects.create(provider="mojang", version="1.20.4")

    assert download.status == "pending"
    assert download.filename == ""
    assert download.error_message == ""


@pytest.mark.django_db
def test_jar_download_str_includes_provider_and_version():
    download = JarDownload.objects.create(provider="paper", version="1.20.4")

    assert "paper" in str(download)
    assert "1.20.4" in str(download)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest mcmanager/console/tests/test_models.py -v -k jar_download`
Expected: FAIL — `ImportError: cannot import name 'JarDownload' from 'mcmanager.console.models'`

- [ ] **Step 3: Add the model**

Append to `mcmanager/console/models.py` (after the `Server` class's `clean()` method, which is the last thing in the file):

```python


class JarDownload(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('downloading', 'Downloading'),
        ('done', 'Done'),
        ('error', 'Error'),
    ]

    provider = models.CharField(max_length=20)
    version = models.CharField(max_length=50)
    filename = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.provider} {self.version} ({self.status})'
```

- [ ] **Step 4: Generate the migration**

Run: `python manage.py makemigrations console`
Expected output includes: `Migrations for 'console': mcmanager\console\migrations\0008_jardownload.py` (Django names a new-model migration `<n>_<modelname lowercase>.py`; if the generated name differs, use the actual generated filename for the rest of this task).

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest mcmanager/console/tests/test_models.py -v -k jar_download`
Expected: `2 passed`

- [ ] **Step 6: Run the full suite to check for regressions**

Run: `python -m pytest -v`
Expected: all tests pass (existing suite plus the 2 new ones).

- [ ] **Step 7: Commit**

```bash
git add mcmanager/console/models.py mcmanager/console/tests/test_models.py mcmanager/console/migrations/0008_jardownload.py
git commit -m "feat: add JarDownload model to track background jar download jobs"
```

---

### Task 2: Mojang and PaperMC provider modules

**Files:**
- Create: `mcmanager/console/services/jar_providers/__init__.py` (empty)
- Create: `mcmanager/console/services/jar_providers/base.py`
- Create: `mcmanager/console/services/jar_providers/mojang.py`
- Create: `mcmanager/console/services/jar_providers/paper.py`
- Test: `mcmanager/console/tests/test_jar_providers.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `mcmanager.console.services.jar_providers.base.VersionInfo(version: str, label: str)` and `DownloadInfo(url: str, filename: str, expected_hash: str, hash_algorithm: str)` as dataclasses. `mcmanager.console.services.jar_providers.mojang.list_versions() -> list[VersionInfo]`, `mojang.get_download_info(version: str) -> DownloadInfo`. Same two function names/signatures in `mcmanager.console.services.jar_providers.paper`. Task 3's orchestrator imports and calls these four functions by exact name.

- [ ] **Step 1: Write the failing tests**

Create `mcmanager/console/tests/test_jar_providers.py`:

```python
import json
from unittest.mock import patch, MagicMock

import pytest

from mcmanager.console.services.jar_providers import mojang, paper

MOJANG_MANIFEST = {
    "versions": [
        {"id": "24w10a", "type": "snapshot", "url": "https://example.com/24w10a.json"},
        {"id": "1.20.4", "type": "release", "url": "https://example.com/1.20.4.json"},
        {"id": "1.20.3", "type": "release", "url": "https://example.com/1.20.3.json"},
        {"id": "1.19.4", "type": "old_beta", "url": "https://example.com/1.19.4.json"},
    ]
}

MOJANG_VERSION_DETAIL = {
    "downloads": {
        "server": {
            "url": "https://example.com/server-1.20.4.jar",
            "sha1": "abc123def456",
        }
    }
}

PAPER_VERSIONS = {"versions": ["1.20.3", "1.20.4"]}

PAPER_BUILDS = {
    "builds": [
        {
            "build": 450,
            "downloads": {"application": {"name": "paper-1.20.4-450.jar", "sha256": "old-hash"}},
        },
        {
            "build": 451,
            "downloads": {"application": {"name": "paper-1.20.4-451.jar", "sha256": "new-hash"}},
        },
    ]
}


def _mock_urlopen_returning(payload):
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(payload).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp
    return mock_resp


def test_mojang_list_versions_filters_to_releases_most_recent_first():
    with patch("urllib.request.urlopen", return_value=_mock_urlopen_returning(MOJANG_MANIFEST)):
        versions = mojang.list_versions()

    assert [v.version for v in versions] == ["1.20.4", "1.20.3"]
    assert all(v.label == v.version for v in versions)


def test_mojang_get_download_info_returns_server_jar_url_and_sha1():
    responses = [_mock_urlopen_returning(MOJANG_MANIFEST), _mock_urlopen_returning(MOJANG_VERSION_DETAIL)]
    with patch("urllib.request.urlopen", side_effect=responses):
        info = mojang.get_download_info("1.20.4")

    assert info.url == "https://example.com/server-1.20.4.jar"
    assert info.expected_hash == "abc123def456"
    assert info.hash_algorithm == "sha1"
    assert info.filename == "vanilla-1.20.4.jar"


def test_mojang_get_download_info_raises_for_unknown_version():
    with patch("urllib.request.urlopen", return_value=_mock_urlopen_returning(MOJANG_MANIFEST)):
        with pytest.raises(ValueError):
            mojang.get_download_info("99.99.99")


def test_paper_list_versions_returns_all_supported_versions():
    with patch("urllib.request.urlopen", return_value=_mock_urlopen_returning(PAPER_VERSIONS)):
        versions = paper.list_versions()

    assert [v.version for v in versions] == ["1.20.3", "1.20.4"]


def test_paper_get_download_info_picks_highest_build_number():
    with patch("urllib.request.urlopen", return_value=_mock_urlopen_returning(PAPER_BUILDS)):
        info = paper.get_download_info("1.20.4")

    assert info.filename == "paper-1.20.4-451.jar"
    assert info.expected_hash == "new-hash"
    assert info.hash_algorithm == "sha256"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest mcmanager/console/tests/test_jar_providers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcmanager.console.services.jar_providers'`

- [ ] **Step 3: Create the base interface**

Create `mcmanager/console/services/jar_providers/__init__.py` (empty file).

Create `mcmanager/console/services/jar_providers/base.py`:

```python
"""Common shapes both jar providers (Mojang, PaperMC) return. Neither
dataclass carries provider-specific knowledge -- the orchestrator in
services/jars.py only ever deals with these two types."""
from dataclasses import dataclass


@dataclass
class VersionInfo:
    version: str
    label: str


@dataclass
class DownloadInfo:
    url: str
    filename: str
    expected_hash: str
    hash_algorithm: str
```

- [ ] **Step 4: Create the Mojang provider**

Create `mcmanager/console/services/jar_providers/mojang.py`:

```python
import json
import urllib.request

from .base import DownloadInfo, VersionInfo

VERSION_MANIFEST_URL = 'https://piston-meta.mojang.com/mc/game/version_manifest_v2.json'


def _fetch_json(url):
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read())


def list_versions():
    manifest = _fetch_json(VERSION_MANIFEST_URL)
    return [
        VersionInfo(version=v['id'], label=v['id'])
        for v in manifest['versions']
        if v['type'] == 'release'
    ]


def get_download_info(version):
    manifest = _fetch_json(VERSION_MANIFEST_URL)
    entry = next((v for v in manifest['versions'] if v['id'] == version), None)
    if entry is None:
        raise ValueError(f'Unknown Mojang version: {version}')

    version_data = _fetch_json(entry['url'])
    server_download = version_data['downloads']['server']
    return DownloadInfo(
        url=server_download['url'],
        filename=f'vanilla-{version}.jar',
        expected_hash=server_download['sha1'],
        hash_algorithm='sha1',
    )
```

- [ ] **Step 5: Create the PaperMC provider**

Create `mcmanager/console/services/jar_providers/paper.py`:

```python
import json
import urllib.request

from .base import DownloadInfo, VersionInfo

PROJECT_URL = 'https://api.papermc.io/v2/projects/paper'


def _fetch_json(url):
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read())


def list_versions():
    data = _fetch_json(PROJECT_URL)
    return [VersionInfo(version=v, label=v) for v in data['versions']]


def get_download_info(version):
    builds_data = _fetch_json(f'{PROJECT_URL}/versions/{version}/builds')
    builds = builds_data['builds']
    if not builds:
        raise ValueError(f'No PaperMC builds found for version: {version}')

    latest_build = max(builds, key=lambda b: b['build'])
    application = latest_build['downloads']['application']
    return DownloadInfo(
        url=f'{PROJECT_URL}/versions/{version}/builds/{latest_build["build"]}/downloads/{application["name"]}',
        filename=application['name'],
        expected_hash=application['sha256'],
        hash_algorithm='sha256',
    )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest mcmanager/console/tests/test_jar_providers.py -v`
Expected: `5 passed`

- [ ] **Step 7: Run the full suite to check for regressions**

Run: `python -m pytest -v`
Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add mcmanager/console/services/jar_providers/ mcmanager/console/tests/test_jar_providers.py
git commit -m "feat: add Mojang and PaperMC jar provider modules"
```

---

### Task 3: Download orchestrator

**Files:**
- Create: `mcmanager/console/services/jars.py`
- Test: `mcmanager/console/tests/test_jars.py`

**Interfaces:**
- Consumes: `mcmanager.console.models.JarDownload` (Task 1); `mcmanager.console.services.jar_providers.{mojang,paper}.{list_versions,get_download_info}` and `jar_providers.base.DownloadInfo` (Task 2); `settings.JAR_DIR` (`mcmanager/settings.py:38`, unchanged).
- Produces: `mcmanager.console.services.jars.list_versions(provider_name: str) -> list[VersionInfo]`, `mcmanager.console.services.jars.start_download(provider_name: str, version: str) -> JarDownload` (the returned instance has a real primary key; the actual download runs asynchronously on a background thread after this function returns). Task 4's views call these two functions by exact name.

- [ ] **Step 1: Write the failing tests**

Create `mcmanager/console/tests/test_jars.py`:

```python
import hashlib
import time
from unittest.mock import patch

import pytest

from mcmanager.console.models import JarDownload
from mcmanager.console.services import jars
from mcmanager.console.services.jar_providers.base import DownloadInfo


def _wait_for_terminal_status(download_id, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        download = JarDownload.objects.get(id=download_id)
        if download.status in ('done', 'error'):
            return download
        time.sleep(0.05)
    raise AssertionError('Download did not reach a terminal status in time')


@pytest.mark.django_db(transaction=True)
def test_start_download_succeeds_with_matching_hash(settings, tmp_path):
    settings.JAR_DIR = tmp_path / 'jar'
    settings.JAR_DIR.mkdir()

    source_file = tmp_path / 'source.jar'
    content = b'fake jar bytes for testing'
    source_file.write_bytes(content)
    expected_hash = hashlib.sha256(content).hexdigest()

    fake_info = DownloadInfo(
        url=source_file.as_uri(),
        filename='test-server.jar',
        expected_hash=expected_hash,
        hash_algorithm='sha256',
    )
    with patch.object(jars.PROVIDERS['mojang'], 'get_download_info', return_value=fake_info):
        download = jars.start_download('mojang', '1.20.4')
        result = _wait_for_terminal_status(download.id)

    assert result.status == 'done'
    assert result.filename == 'test-server.jar'
    downloaded_path = settings.JAR_DIR / 'test-server.jar'
    assert downloaded_path.exists()
    assert downloaded_path.read_bytes() == content
    assert not (settings.JAR_DIR / 'test-server.jar.part').exists()


@pytest.mark.django_db(transaction=True)
def test_start_download_fails_and_cleans_up_on_hash_mismatch(settings, tmp_path):
    settings.JAR_DIR = tmp_path / 'jar'
    settings.JAR_DIR.mkdir()

    source_file = tmp_path / 'source.jar'
    source_file.write_bytes(b'fake jar bytes for testing')

    fake_info = DownloadInfo(
        url=source_file.as_uri(),
        filename='bad-server.jar',
        expected_hash='0000000000000000000000000000000000000000000000000000000000000000',
        hash_algorithm='sha256',
    )
    with patch.object(jars.PROVIDERS['mojang'], 'get_download_info', return_value=fake_info):
        download = jars.start_download('mojang', '1.20.4')
        result = _wait_for_terminal_status(download.id)

    assert result.status == 'error'
    assert 'hash' in result.error_message.lower() or 'checksum' in result.error_message.lower()
    assert not (settings.JAR_DIR / 'bad-server.jar').exists()
    assert not (settings.JAR_DIR / 'bad-server.jar.part').exists()


@pytest.mark.django_db(transaction=True)
def test_start_download_records_error_when_provider_raises(settings, tmp_path):
    settings.JAR_DIR = tmp_path / 'jar'
    settings.JAR_DIR.mkdir()

    with patch.object(jars.PROVIDERS['mojang'], 'get_download_info', side_effect=ValueError('Unknown version')):
        download = jars.start_download('mojang', 'not-a-real-version')
        result = _wait_for_terminal_status(download.id)

    assert result.status == 'error'
    assert 'Unknown version' in result.error_message


@pytest.mark.django_db
def test_list_versions_delegates_to_the_named_provider():
    from mcmanager.console.services.jar_providers.base import VersionInfo

    with patch.object(jars.PROVIDERS['paper'], 'list_versions', return_value=[VersionInfo('1.20.4', '1.20.4')]):
        versions = jars.list_versions('paper')

    assert versions == [VersionInfo('1.20.4', '1.20.4')]
```

Note: `@pytest.mark.django_db(transaction=True)` is required (not plain `@pytest.mark.django_db`) for the three tests that wait on a background thread — the background thread's `JarDownload.objects.get(...)`/`.save()` calls need to see committed data from a separate DB connection, which plain `django_db`'s wrapping transaction would hide from the polling `_wait_for_terminal_status` loop running against a different connection.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest mcmanager/console/tests/test_jars.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcmanager.console.services.jars'`

- [ ] **Step 3: Implement the orchestrator**

Create `mcmanager/console/services/jars.py`:

```python
"""Orchestrates jar downloads: resolves a version to a download URL+hash via
the chosen provider, streams the file to JAR_DIR, verifies the checksum, and
only makes the file visible (via an atomic rename) once it's verified."""
import hashlib
import threading
import urllib.request

from django.conf import settings

from ..models import JarDownload
from .jar_providers import mojang, paper

PROVIDERS = {'mojang': mojang, 'paper': paper}


def list_versions(provider_name):
    return PROVIDERS[provider_name].list_versions()


def start_download(provider_name, version):
    download = JarDownload.objects.create(provider=provider_name, version=version, status='pending')
    thread = threading.Thread(target=_run_download, args=(download.id,), daemon=True)
    thread.start()
    return download


def _run_download(download_id):
    download = JarDownload.objects.get(id=download_id)
    download.status = 'downloading'
    download.save(update_fields=['status'])

    try:
        info = PROVIDERS[download.provider].get_download_info(download.version)
        dest_path = settings.JAR_DIR / info.filename
        tmp_path = dest_path.with_suffix(dest_path.suffix + '.part')
        hasher = hashlib.new(info.hash_algorithm)

        with urllib.request.urlopen(info.url, timeout=30) as resp, open(tmp_path, 'wb') as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                hasher.update(chunk)

        if hasher.hexdigest() != info.expected_hash:
            tmp_path.unlink(missing_ok=True)
            download.status = 'error'
            download.error_message = 'Hash mismatch -- downloaded file did not match expected checksum'
            download.save(update_fields=['status', 'error_message'])
            return

        tmp_path.rename(dest_path)
        download.filename = info.filename
        download.status = 'done'
        download.save(update_fields=['filename', 'status'])
    except Exception as exc:
        download.status = 'error'
        download.error_message = str(exc)
        download.save(update_fields=['status', 'error_message'])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest mcmanager/console/tests/test_jars.py -v`
Expected: `4 passed`

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `python -m pytest -v`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add mcmanager/console/services/jars.py mcmanager/console/tests/test_jars.py
git commit -m "feat: add background jar download orchestrator with checksum verification"
```

---

### Task 4: Views, URLs, and the jars management page

**Files:**
- Create: `mcmanager/console/views_jars.py`
- Modify: `mcmanager/console/urls.py`
- Create: `mcmanager/console/templates/console/jars.html`
- Modify: `mcmanager/console/templates/index.html`
- Test: `mcmanager/console/tests/test_views_jars.py`

**Interfaces:**
- Consumes: `mcmanager.console.services.jars.{list_versions,start_download}` (Task 3); `mcmanager.console.models.JarDownload` (Task 1).
- Produces: no new interfaces — this is the last task in this plan.

- [ ] **Step 1: Write the failing tests**

Create `mcmanager/console/tests/test_views_jars.py`:

```python
from unittest.mock import patch

import pytest

from mcmanager.console.models import JarDownload
from mcmanager.console.services.jar_providers.base import VersionInfo


@pytest.fixture
def staff_client(client, django_user_model):
    staff = django_user_model.objects.create_user(username="admin", password="pw", is_staff=True)
    client.force_login(staff)
    return client


@pytest.mark.django_db
def test_jars_page_requires_staff_login(client):
    resp = client.get("/console/jars/")
    assert resp.status_code == 302


@pytest.mark.django_db
def test_jars_page_loads_for_staff(staff_client):
    resp = staff_client.get("/console/jars/")
    assert resp.status_code == 200


@pytest.mark.django_db
def test_list_jar_versions_returns_versions_from_provider(staff_client):
    with patch(
        "mcmanager.console.views_jars.jars.list_versions",
        return_value=[VersionInfo("1.20.4", "1.20.4")],
    ):
        resp = staff_client.get("/console/jars/versions/mojang")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["versions"] == [{"version": "1.20.4", "label": "1.20.4"}]


@pytest.mark.django_db
def test_list_jar_versions_returns_clean_error_on_provider_failure(staff_client):
    with patch(
        "mcmanager.console.views_jars.jars.list_versions",
        side_effect=RuntimeError("network down"),
    ):
        resp = staff_client.get("/console/jars/versions/mojang")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "error"


@pytest.mark.django_db
def test_start_jar_download_creates_job_and_returns_id(staff_client):
    with patch("mcmanager.console.views_jars.jars.start_download") as mock_start:
        mock_start.return_value = JarDownload.objects.create(provider="mojang", version="1.20.4")

        resp = staff_client.post("/console/jars/download", {"provider": "mojang", "version": "1.20.4"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert "download_id" in body
    mock_start.assert_called_once_with("mojang", "1.20.4")


@pytest.mark.django_db
def test_start_jar_download_requires_post(staff_client):
    resp = staff_client.get("/console/jars/download")
    assert resp.status_code == 405


@pytest.mark.django_db
def test_start_jar_download_rejects_unknown_provider(staff_client):
    resp = staff_client.post("/console/jars/download", {"provider": "bogus", "version": "1.20.4"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "error"


@pytest.mark.django_db
def test_jar_download_status_returns_current_state(staff_client):
    download = JarDownload.objects.create(
        provider="paper", version="1.20.4", status="done", filename="paper-1.20.4-451.jar"
    )

    resp = staff_client.get(f"/console/jars/download/{download.id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["download_status"] == "done"
    assert body["filename"] == "paper-1.20.4-451.jar"


@pytest.mark.django_db
def test_jar_download_status_returns_error_for_unknown_id(staff_client):
    resp = staff_client.get("/console/jars/download/999999")
    assert resp.status_code == 200
    assert resp.json()["status"] == "error"
```

There are two definitions of `test_jar_download_status_returns_current_state` above (the first is a no-op placeholder immediately overridden by the second, real one, since Python keeps only the last function with a given name at module scope) — delete the first, no-op one before saving the file. Only the second (real) definition belongs in the final test file.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest mcmanager/console/tests/test_views_jars.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcmanager.console.views_jars'` (and `django.urls.exceptions.NoReverseMatch`/404s once that's fixed, since the URLs don't exist yet either).

- [ ] **Step 3: Create the views**

Create `mcmanager/console/views_jars.py`:

```python
from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from .models import JarDownload
from .services import jars

VALID_PROVIDERS = ('mojang', 'paper')


@staff_member_required
def jars_page(request):
    return render(request, 'console/jars.html', {})


@staff_member_required
def list_jar_versions(request, provider):
    if provider not in VALID_PROVIDERS:
        return JsonResponse({'status': 'error', 'message': f'Unknown provider: {provider}'})
    try:
        versions = jars.list_versions(provider)
    except Exception as exc:
        return JsonResponse({'status': 'error', 'message': str(exc)})
    return JsonResponse({
        'status': 'success',
        'versions': [{'version': v.version, 'label': v.label} for v in versions],
    })


@staff_member_required
@require_POST
def start_jar_download(request):
    provider = request.POST.get('provider')
    version = request.POST.get('version')
    if provider not in VALID_PROVIDERS or not version:
        return JsonResponse({'status': 'error', 'message': 'Invalid provider or version'})
    download = jars.start_download(provider, version)
    return JsonResponse({'status': 'success', 'download_id': download.id})


@staff_member_required
def jar_download_status(request, download_id):
    try:
        download = JarDownload.objects.get(id=download_id)
    except JarDownload.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'Download not found'})
    return JsonResponse({
        'status': 'success',
        'download_status': download.status,
        'error_message': download.error_message,
        'filename': download.filename,
    })
```

- [ ] **Step 4: Add the URLs**

Replace the full contents of `mcmanager/console/urls.py` with:

```python
from django.urls import path
from . import views, views_jars

urlpatterns = [
    path('<int:id>', views.index, name='index'),
    path('start_server/<int:id>', views.start_server, name='start_server'),
    path('stop_server/<int:id>', views.stop_server, name='stop_server'),
    path('force_stop_server/<int:id>',
         views.force_stop_server, name='force_stop_server'),
    path('view_logs/<int:id>', views.view_logs, name='view_logs'),
    path('send_command/<int:id>', views.send_command, name='send_command'),
    path('get_server_stats/<int:id>', views.get_server_stats, name='get_server_stats'),
    path('jars/', views_jars.jars_page, name='jars_page'),
    path('jars/versions/<str:provider>', views_jars.list_jar_versions, name='list_jar_versions'),
    path('jars/download', views_jars.start_jar_download, name='start_jar_download'),
    path('jars/download/<int:download_id>', views_jars.jar_download_status, name='jar_download_status'),
]
```

- [ ] **Step 5: Create the template**

Create `mcmanager/console/templates/console/jars.html`:

```html
{% load static %}
<!DOCTYPE html>
<html lang="pt-br">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Download de Jars</title>
        <link rel="icon" type="image/x-icon" href="{% static 'img/favicon.ico' %}">
        <link href="{% static 'css/tailwind.css' %}" rel="stylesheet">
        <link href="{% static 'css/toastify.min.css' %}" rel="stylesheet" />
        <link href="{% static 'css/font-awesome.min.css' %}" rel="stylesheet" />
    </head>
    <body class="bg-gray-100 p-6">
        <div class="container mx-auto">
            <a href="{% url 'home' %}">
                <button class="bg-blue-500 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded">
                    <i class="fa fa-arrow-left" aria-hidden="true"></i>
                </button>
            </a>
            <h1 class="text-3xl font-bold mb-4">Download de Jars</h1>
            <form id="download-form" class="mb-4">
                {% csrf_token %}
                <select id="provider" class="shadow border rounded py-2 px-3 mr-2">
                    <option value="mojang">Vanilla (Mojang)</option>
                    <option value="paper">Paper</option>
                </select>
                <select id="version" class="shadow border rounded py-2 px-3 mr-2"></select>
                <button type="submit" class="bg-blue-500 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded">
                    Baixar
                </button>
            </form>
            <div id="download-status" class="mt-4"></div>
        </div>
        <script type="text/javascript" src="{% static 'js/toastify-js.js' %}"></script>
        <script>
            function getCsrfToken() {
                return document.querySelector('[name=csrfmiddlewaretoken]').value;
            }

            function create_toast(message, bgColor, textColor) {
                Toastify({
                    text: message,
                    duration: 4000,
                    close: true,
                    gravity: "top",
                    position: "right",
                    stopOnFocus: true,
                    style: { background: bgColor, color: textColor },
                }).showToast();
            }

            function loadVersions() {
                const provider = document.getElementById('provider').value;
                fetch(`/console/jars/versions/${provider}`)
                    .then(response => response.json())
                    .then(data => {
                        const versionSelect = document.getElementById('version');
                        versionSelect.innerHTML = '';
                        if (data.status === 'success') {
                            data.versions.forEach(v => {
                                const opt = document.createElement('option');
                                opt.value = v.version;
                                opt.textContent = v.label;
                                versionSelect.appendChild(opt);
                            });
                        } else {
                            create_toast(data.message, 'red', 'white');
                        }
                    });
            }

            document.getElementById('provider').addEventListener('change', loadVersions);
            loadVersions();

            let statusPoll = null;

            document.getElementById('download-form').addEventListener('submit', function(event) {
                event.preventDefault();
                const provider = document.getElementById('provider').value;
                const version = document.getElementById('version').value;
                const formData = new FormData();
                formData.append('provider', provider);
                formData.append('version', version);

                fetch('/console/jars/download', {
                    method: 'POST',
                    body: formData,
                    headers: { 'X-CSRFToken': getCsrfToken() },
                })
                    .then(response => response.json())
                    .then(data => {
                        if (data.status === 'success') {
                            pollStatus(data.download_id);
                        } else {
                            create_toast(data.message, 'red', 'white');
                        }
                    });
            });

            function pollStatus(downloadId) {
                if (statusPoll !== null) {
                    clearInterval(statusPoll);
                }
                document.getElementById('download-status').textContent = 'Baixando...';
                statusPoll = setInterval(() => {
                    fetch(`/console/jars/download/${downloadId}`)
                        .then(response => response.json())
                        .then(data => {
                            document.getElementById('download-status').textContent = data.download_status;
                            if (data.download_status === 'done') {
                                clearInterval(statusPoll);
                                create_toast('Jar baixado: ' + data.filename, 'green', 'white');
                            } else if (data.download_status === 'error') {
                                clearInterval(statusPoll);
                                create_toast(data.error_message, 'red', 'white');
                            }
                        });
                }, 2000);
            }
        </script>
    </body>
</html>
```

- [ ] **Step 6: Link the new page from the home page**

In `mcmanager/console/templates/index.html`, add a link right after the closing `</form>` tag (currently line 38):

```html
            </form>
            <a href="{% url 'jars_page' %}" class="text-blue-500 hover:underline mt-4 inline-block">
                Gerenciar jars
            </a>
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `python -m pytest mcmanager/console/tests/test_views_jars.py -v`
Expected: `9 passed`

- [ ] **Step 8: Run the full suite to check for regressions**

Run: `python -m pytest -v`
Expected: all tests pass.

- [ ] **Step 9: Manually verify in a browser**

This step touches real network calls to the Mojang/PaperMC APIs, which the automated suite (correctly) mocks out. On a machine with internet access:

```bash
python manage.py createsuperuser
python manage.py runserver
```

Log in, go to `/console/jars/`, confirm the version dropdown populates for both Mojang and Paper (switch the provider dropdown and watch it reload), start a real download of a small/recent version, and confirm the status updates from "downloading" to "done" and the file actually appears in your configured `JAR_DIR`. Then go create/edit a server and confirm the newly-downloaded jar shows up in the `jar_template` dropdown.

- [ ] **Step 10: Commit**

```bash
git add mcmanager/console/views_jars.py mcmanager/console/urls.py mcmanager/console/templates/console/jars.html mcmanager/console/templates/index.html mcmanager/console/tests/test_views_jars.py
git commit -m "feat: add jar download management page with provider/version selection"
```

---

## Self-review notes

- **Spec coverage:** Task 1 covers the model. Task 2 covers both providers (release-filtering for Mojang, highest-build selection for Paper, sha1/sha256 respectively). Task 3 covers the orchestrator (background thread, `.part` staging + atomic rename, hash verification and cleanup on mismatch, error capture). Task 4 covers all 4 endpoints, the template (provider/version dropdowns, polling, toasts), and the home-page link. Every constraint from the spec's Decision Log has a concrete line of code implementing it.
- **Placeholder scan:** found and fixed one during this review — Task 4's test file draft had a duplicate `test_jar_download_status_returns_current_state` (a leftover no-op stub immediately followed by the real test); removed the stub directly so the plan text shows only the final, correct test file content.
- **Type consistency:** `VersionInfo(version, label)` and `DownloadInfo(url, filename, expected_hash, hash_algorithm)` are used with the same field names and order across Task 2 (definition), Task 2's own tests, Task 3 (orchestrator + tests), and Task 4 (view tests' mock return values). `jars.list_versions`/`jars.start_download` signatures match between Task 3's definition and Task 4's view calls (`jars.list_versions(provider)`, `jars.start_download(provider, version)`). `JarDownload`'s field names (`provider`, `version`, `filename`, `status`, `error_message`) are consistent across Task 1's definition and every later task's usage.
