# Dependency Vulnerability Triage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close all 30 Dependabot vulnerabilities flagged on `origin/main` by syncing `requirements.txt` with the Django/sqlparse versions already installed and tested in this environment, and running `npm audit fix` on the tailwindcss build toolchain's lockfile.

**Architecture:** Two independent, sequential file-only changes — no application code changes. Task 1 touches the Python dependency pin (`requirements.txt`). Task 2 touches the npm lockfile (`package-lock.json`). Each task's own verification step is the test that the dependency bump didn't break anything.

**Tech Stack:** pip (`requirements.txt`), npm (`package-lock.json`), pytest/pytest-django, Node.js `npm` CLI (Windows dev environment, `node`/`npm` already on PATH — confirmed working in this session).

## Global Constraints

- Target Django version: `5.1.15` (already installed in this dev environment, confirmed via `pip show Django` — do NOT pick a different/later patch version even if one has since been released; use exactly `5.1.15` per the approved spec).
- Target sqlparse version: `5.5` matching module version string `0.5.5` (already installed, confirmed via `pip show sqlparse`).
- `pyproject.toml`'s existing constraint `Django>=4.2.0,<5.2.0` must NOT be modified — `5.1.15` already satisfies it.
- `requirements-dev.txt` must NOT be modified — it inherits the fix via its existing `-r requirements.txt` line.
- `npm audit fix` must be run WITHOUT `--force` — every currently-flagged vulnerability in the npm tree has a non-forcing fix available (confirmed during brainstorming via `npm audit`); a `--force` run would be a scope violation requiring a new decision.
- `package.json`'s direct devDependency line (`"tailwindcss": "^3.4.4"`) must NOT be edited — only the lockfile changes.
- No application code (Python or template) changes are in scope for this plan.

---

### Task 1: Sync `requirements.txt` to installed Django/sqlparse versions

**Files:**
- Modify: `requirements.txt`
- Test: full existing suite via `pytest` (no new test file — this task has no new behavior, only a version-pin change; the existing 139-test suite is the regression check)

**Interfaces:**
- Consumes: nothing from other tasks (this task is independent of Task 2).
- Produces: nothing consumed by Task 2 (the two tasks are independent; order between them does not matter, but Task 1 is listed first as it addresses the larger share of alerts — 26 of 30).

- [ ] **Step 1: Confirm current installed versions match the plan's target**

Run:
```bash
python -m pip show Django sqlparse
```
Expected output includes exactly:
```
Name: Django
Version: 5.1.15
...
Name: sqlparse
Version: 0.5.5
```
If either version differs from this, STOP and report back before proceeding — the plan's Global Constraints assume these exact installed versions; do not silently substitute a different one.

- [ ] **Step 2: Edit `requirements.txt`**

Current content:
```
asgiref==3.8.1
Django==4.0.5
psutil==6.0.0
sqlparse==0.5.0
typing_extensions==4.12.2
autopep8==2.3.1
djlint==1.34.1
```

Change to:
```
asgiref==3.8.1
Django==5.1.15
psutil==6.0.0
sqlparse==0.5.5
typing_extensions==4.12.2
autopep8==2.3.1
djlint==1.34.1
```

Only the `Django` and `sqlparse` lines change. Every other line stays identical, including trailing whitespace/lack thereof.

- [ ] **Step 3: Run the full test suite**

Run:
```bash
python -m pytest -v
```
Expected: all 139 tests pass (0 failed, 0 errors). This environment already runs Django 5.1.15 and sqlparse 0.5.5 in practice (this is a pin-sync, not an actual dependency upgrade), so no behavior change is expected. If ANY test fails or errors that did not fail before this change, STOP and report — do not attempt to fix application code as part of this task; that would mean the spec's "no code changes expected" assumption was wrong and needs to go back to the human.

- [ ] **Step 4: Run Django's own system check**

Run:
```bash
python manage.py check
```
Expected: `System check identified no issues (0 silenced).`

- [ ] **Step 5: Commit**

```bash
git add requirements.txt
git commit -m "fix: sync requirements.txt Django/sqlparse pins to installed, patched versions"
```

---

### Task 2: Resolve npm-side vulnerabilities via `npm audit fix`

**Files:**
- Modify: `package-lock.json`
- Test: `npm run build:css` (manual verification step, no automated test — this repo has no JS test suite; the build command succeeding is the regression check)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: nothing consumed by later tasks (this is the final task in this plan).

- [ ] **Step 1: Run `npm audit` to record the baseline**

Run:
```bash
npm audit
```
Expected: reports vulnerabilities in the `glob`/`minimatch`/`postcss`/`picomatch`/`brace-expansion`/`cross-spawn`/`micromatch`/`nanoid`/`yaml` dependency chain (9 total: 4 moderate, 5 high, per the approved spec), each line ending with `fix available via npm audit fix` and none requiring `--force`.

If any vulnerability in this output says it requires `--force` to fix (i.e. does NOT say "fix available via `npm audit fix`" without a force qualifier), STOP and report back — per Global Constraints, `--force` is out of scope for this plan and needs a new decision from the human.

- [ ] **Step 2: Apply the fix**

Run:
```bash
npm audit fix
```
Expected: npm reports it updated some number of packages and rewrote `package-lock.json`. Do NOT pass `--force`.

- [ ] **Step 3: Confirm the audit is now clean**

Run:
```bash
npm audit
```
Expected: `found 0 vulnerabilities`. If any vulnerability remains that `npm audit fix` could not resolve without `--force`, do not force it — instead, note exactly which package/advisory remains and report back for a decision, rather than silently leaving it unresolved and unreported.

- [ ] **Step 4: Verify the CSS build still works**

Run:
```bash
npm run build:css
```
Expected: exits successfully (exit code 0) and `static/css/tailwind.css` is written/updated with no error output.

- [ ] **Step 5: Confirm no production dependency was touched**

Run:
```bash
git diff package-lock.json | grep -E '^\+' | grep -i '"dev": false' | head -20
```
Expected: no output (this repo's `package.json` declares only a `devDependencies` entry, so every package in the lockfile should already be marked `"dev": true`; this step is a sanity check that `npm audit fix` didn't introduce a new non-dev entry). If this command produces any output, STOP and report back before committing.

- [ ] **Step 6: Commit**

```bash
git add package-lock.json
git commit -m "fix: resolve npm audit vulnerabilities in tailwindcss build toolchain"
```
