# mcmanager Phase 2 — Cross-Platform Process Manager — Design Spec

> Produced via `superpowers:brainstorming` session on 2026-07-17, immediately following Phase 1's completion and merge to `origin/main` (commit `7c6e1a1`).
> Status: **Approved** (all sections confirmed by the maintainer).
> Parent design: `docs/design-melhorias.md` (Phase 2 section, high-level decisions already locked there).

---

## 1. Understanding Summary

- **What:** replace mcmanager's Linux-only, PTY/`/tmp`-based process handling with a single, testable `services/process.py` module built on `subprocess.Popen` (cross-platform spawn) and a self-implemented RCON client (cross-platform commands/graceful stop).
- **Why:** unblock Windows support, remove the last shell/PTY-shaped attack surface, and give Phase 3/4 features (console, backups, auto-restart) a stable, testable foundation.
- **Who:** same audience as Phase 1 — `pip install mcmanager` users on Linux VPS or Windows.
- **Scale/constraints:** 1 machine, ~1–10 servers; solo maintainer; no new heavyweight dependencies.
- **Non-goals this phase:** real-time console, jar downloads, backups, auto-restart/scheduling (all Phase 3). No player-list endpoint yet (RCON `list` becomes available as a raw command, Phase 4 builds a dedicated UI on top of it later).

## 2. Decisions Locked This Session

| # | Decision | Alternatives considered | Why |
|---|----------|--------------------------|-----|
| 1 | RCON client: self-implemented (~80 lines, raw Source RCON protocol over TCP) | `mctools` (third-party dep); `mcrcon` | Zero new PyPI dependency for a simple protocol; full control for testing |
| 2 | `rcon_port`/`rcon_password` as new fields on `Server` | Derive port from game port at read-time only (no field); keep only in `server.properties` file | DB fields are queryable/visible without opening a file each time, consistent with how the rest of the model works |
| 3 | `rcon_port = server.port + 10000` | Separate incrementing counter/allocator | Simple, no extra allocation state; collisions are exceedingly unlikely at 1–10 servers/machine |
| 4 | Migration `0006` backfills existing servers automatically (generates password/port, rewrites `server.properties`) | Generate lazily on next start; treat as a hard beta-compatibility break requiring server recreation | Least disruptive path for the (small) existing beta install base |
| 5 | Skip (not block) migration backfill for servers running at migration time, log a warning to re-run later | Abort the whole migration if any server is running | Upgrade normally happens with everything stopped; a hard abort is disproportionate for a rare case |
| 6 | `Server.status` field **removed**; "is it running" becomes a live-computed property calling `process.is_running(server)` | Keep `status` as a DB cache, synced by the Phase 3 supervisor thread | Removes an entire class of drift bugs (this was the direct cause of the DB-touching `ConsoleConfig.ready()` issue flagged during Phase 1); at this scale, a live `psutil`/file check per request has no real performance cost |
| 7 | `pty.fork()` removed entirely, including on Linux — `subprocess.Popen` everywhere | Keep `pty.fork` on Linux as a Linux-specific fast path, `Popen` only on Windows | One code path to test and maintain; RCON fully replaces what the PTY was providing (command injection, output capture via log file instead of PTY read) |
| 8 | `ServerProcessManager` becomes plain functions in `services/process.py` (`start`, `stop`, `force_stop`, `send_command`, `is_running`, `get_stats`), not a class | Class instantiated per-`Server` | No cross-call state worth holding in an instance (Django requests are stateless across workers); matches the existing `services/provisioning.py` function-module pattern from Phase 1 |
| 9 | RCON: new TCP connection per call, no persistent pool | Persistent connection cached per server | Admin-level usage volume; a pool adds lifecycle complexity (reconnect on drop, thread-safety across workers) for no measurable benefit here |
| 10 | RCON timeout: fixed 5s constant, not per-server configurable | Configurable timeout field on `Server` | YAGNI — no evidence 5s is ever wrong for a local RCON call |
| 11 | `process.stop()` does not auto-escalate to `force_stop()` on timeout | Auto-kill after N seconds if graceful stop doesn't finish | Avoids killing a process mid-save (world corruption risk); user makes the explicit call to force-stop |
| 12 | Process state file: `~/.mcmanager/run/server_<id>.json` (`{pid, started_at, jar}`), replacing `/tmp/minecraft_server_<id>.pid`/`.pty` | Keep `/tmp` (rejected in Phase 1 planning already — Linux-only, volatile, shared across OS users) | Already decided in the original Phase 1/2 brainstorm; reconfirmed here |
| 13 | Process liveness check validates PID **and** cmdline contains the expected jar name (via `psutil`) | PID existence only (current Phase 1 behavior) | Avoids false positives from a recycled PID reused by an unrelated OS process |
| 14 | Test doubles: a fake Python script standing in for `java -jar`, which also speaks minimal RCON, used in `process.py`/`rcon.py` tests | Mock `subprocess.Popen` and the RCON client entirely | Exercises real OS process/socket behavior (signals, process groups, actual bytes over a real socket) instead of hiding it behind mocks |

## 3. Architecture

```
mcmanager/console/
├── services/
│   ├── process.py       # start / stop / force_stop / send_command / is_running / get_stats
│   └── rcon.py           # self-contained RCON protocol client, no Django/Server knowledge
├── models.py              # Server: + rcon_port, rcon_password (editable=False); - status
├── apps.py                # ConsoleConfig.ready(): status-sync loop removed (nothing left to sync)
├── migrations/
│   └── 0006_*.py          # AddField rcon_port/rcon_password (+ RunPython backfill), RemoveField status
└── tests/
    ├── fixtures/
    │   └── fake_server.py     # stand-in for `java -jar`: accepts -Xms/-Xmx/-jar args, serves minimal RCON
    ├── test_rcon.py
    └── test_process.py
```

`rcon.py` is a pure protocol module: `execute(host, port, password, command, timeout=5.0) -> str`, raising `RconAuthError` / `RconConnectionError` / `RconTimeoutError`. It knows nothing about Django, `Server`, or the filesystem.

`process.py` is the orchestration layer: knows about `Server` model fields, builds the Java command line, manages the `run/server_<id>.json` state file, and calls into `rcon.py` using the server's `rcon_port`/`rcon_password`. It raises its own exceptions (`AlreadyRunningError`, `ProcessNotRunningError`, `JavaNotFoundError`, `StopTimeoutError`, plus the propagated RCON exceptions) — it never constructs an HTTP response.

State file, e.g. `~/.mcmanager/run/server_3.json`:
```json
{"pid": 12345, "started_at": "2026-07-17T14:30:00Z", "jar": "3_paper-1.20.jar"}
```
`run/` is created alongside `servers/`, `jar/`, `configs/` in `settings.py`/`cli.py init`, following the Phase 1 pattern.

No in-memory state between calls anywhere in `process.py` or `rcon.py` — every call re-reads the state file and re-opens a fresh RCON socket, so behavior is identical across Django's multiple worker processes.

## 4. Model & Migration

`Server` model changes:
```python
rcon_port = models.IntegerField(editable=False)
rcon_password = models.CharField(max_length=32, editable=False)
# status: REMOVED
```

`services/provisioning.py:create_server_files()` (Phase 1) gains two responsibilities when first provisioning a server:
1. Generate `rcon_password` via `django.utils.crypto.get_random_string(24, ...)` (same pattern already used for `SECRET_KEY` in `settings.py`).
2. Compute `rcon_port = server.port + 10000` and write `enable-rcon=true`, `rcon.port=<rcon_port>`, `rcon.password=<rcon_password>`, `rcon.bind=127.0.0.1` into `server.properties` alongside the existing `server-port=` rewrite.

Migration `0006`:
- `AddField` for `rcon_port`/`rcon_password` with a temporary `default=` (fields are non-nullable).
- `RunPython` that, for every existing `Server`: generates password + computes port, rewrites `server.properties` on disk — **only if the server isn't currently running** (checked via the new `process.is_running()`); running servers are skipped with a logged warning to re-run the migration after stopping them.
- `RemoveField` for `status`.

Admin: `list_display` drops `'status'` in favor of a computed `is_running` property that delegates to `process.is_running(self)`.

## 5. Process Lifecycle

**`process.start(server)`:**
```python
cmd = [settings.JAVA_BIN_PATH, f"-Xms{server.memory_limit}M", f"-Xmx{server.memory_limit}M", "-jar", server.jar, "nogui"]
kwargs = {"cwd": server_dir, "stdout": log_file, "stderr": subprocess.STDOUT}
if os.name == "posix":
    kwargs["start_new_session"] = True
else:
    kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
proc = subprocess.Popen(cmd, **kwargs)
```
Raises `AlreadyRunningError` if `is_running(server)` is already true (checked first). On success, writes the state JSON (`pid`, `started_at`, `jar`). `log_file` is the same `logs/latest.log` as today, now populated via `stdout=` redirection instead of PTY output. `FileNotFoundError` from `Popen` (missing/invalid Java binary) is caught and re-raised as `JavaNotFoundError` with a clear message.

**`process.stop(server)` (graceful):** calls `rcon.execute(..., "stop")`, then polls `psutil.Process(pid).wait(timeout=30)` (fixed 30s constant, same YAGNI reasoning as the RCON timeout — not per-server configurable) for the process to actually exit before removing the state file. On timeout, raises `StopTimeoutError` directing the caller to force-stop — does not auto-escalate to a kill (avoids interrupting an in-progress world save).

**`process.force_stop(server)`:** `psutil.Process(pid).kill()`, same as Phase 1's implementation, now reading the PID from the JSON state file instead of the `/tmp/*.pid` file.

**`process.is_running(server)`:** reads the state file; if absent, `False`. If present, confirms via `psutil` both that the PID exists **and** that the process's cmdline contains the expected jar filename — guards against a recycled PID belonging to an unrelated process.

## 6. RCON & Commands

```python
def execute(host: str, port: int, password: str, command: str, timeout: float = 5.0) -> str:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        _send_packet(sock, 3, password.encode())   # login (packet type 3)
        if _read_packet(sock)[1] == -1:
            raise RconAuthError("RCON authentication failed")
        _send_packet(sock, 2, command.encode())      # command (packet type 2)
        return _read_packet(sock)[2].decode(errors="replace")
```

`process.send_command(server, command)` calls `rcon.execute(...)` and returns the actual server response (Phase 1's PTY-based approach couldn't do this). Raises `ProcessNotRunningError` up front if the server isn't running, without attempting an RCON connection.

`process.get_stats(server)` is unchanged in approach from Phase 1 — `psutil.Process(pid)` for CPU/memory — just reads the PID from the new state file instead of the old `.pid` file. Does not use RCON.

RCON `list` (online players) becomes usable via `send_command` immediately; Phase 4 builds a dedicated dashboard widget on top of it later — no extra work needed here.

## 7. Views Integration & Error Handling

Every control view in `views.py` becomes a thin wrapper translating `process.py` exceptions into the existing JSON contract:

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

URL names and the `{status, message, ...}` JSON shape are unchanged from Phase 1 — this phase only changes what happens *inside* each view.

`is_server_running` (the Phase 1 helper function in `views.py`) is deleted; every caller (views, `index`) switches to `process.is_running(server)`.

Edge cases covered this phase (others — port-in-use detection, "broken" status for a missing jar file — remain Phase 4 per the original design):
- Missing/invalid Java binary → `JavaNotFoundError`, clear message, no crash.
- RCON unreachable or slow → bounded by the 5s timeout (Section 6), never hangs a request indefinitely.
- Corrupt/unreadable `run/server_<id>.json` → treated as "not running" (same defensive posture as Phase 1's `.pid` file handling).

## 8. Testing Strategy

- `test_rcon.py`: unit tests against `rcon.py` using a minimal fake RCON server (a tiny `socketserver`-based stub in `tests/fixtures/`) — verifies packet framing, auth success/failure, timeout behavior, all without touching `process.py` or Django models.
- `test_process.py`: integration tests using `tests/fixtures/fake_server.py`, a real Python script that mimics `java -jar` closely enough to exercise the real lifecycle — accepts the same `-Xms`/`-Xmx`/-jar` argument shape, writes to stdout (captured as the "log file"), and runs the same minimal RCON stub so `process.stop()`/`send_command()` can be tested against a real socket, not a mock. Covers: start → is_running → send_command → stop (graceful) → is_running is False; start → force_stop → is_running is False; is_running is False when no state file exists; is_running is False when the state file's PID belongs to an unrelated process (recycled-PID guard).
- Windows-specific process-group behavior (`CREATE_NEW_PROCESS_GROUP`) is exercised naturally by the CI matrix (Phase 4 already plans Linux+Windows CI) — no Windows-only test logic needed beyond what the OS-conditional `kwargs` already provides.
- Migration `0006`'s `RunPython` backfill gets its own test: create a `Server` via the pre-migration state (no `rcon_port`/`rcon_password`), run the migration forward, assert the fields are populated and `server.properties` was rewritten.

## 9. Out of Scope (confirmed Non-Goals for This Phase)

- Real-time console, jar downloads, backups, auto-restart/scheduling — all Phase 3.
- Dedicated "online players" UI — Phase 4, built on the `list` RCON command this phase already exposes.
- Port-in-use detection, "broken server" status, structured panel-wide logging — Phase 4.
- RCON connection pooling/persistence, per-server configurable timeouts — YAGNI, no evidence needed at this scale.

## 10. Notable Side Effect

Removing `Server.status` in favor of a live-computed property eliminates the underlying cause of a data-safety issue flagged during Phase 1's review: `ConsoleConfig.ready()` (`mcmanager/console/apps.py`) ran a `Server.objects.all()` query + `.save()` loop at Django app-registry population time, purely to keep `status` in sync — a loop that could run against the real (non-test-isolated) data directory before pytest's isolation fixes took effect. With `status` gone, that loop has no reason to exist and should be removed as part of this phase's `apps.py` cleanup (a two-line change: delete the loop, since there's nothing left to sync). This resolves the follow-up task flagged during Phase 1 (spawned task `task_7b3489a0`) as a natural consequence of this phase's design, not as extra scope.
