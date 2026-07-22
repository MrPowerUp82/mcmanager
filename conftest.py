import os
import sys
import tempfile

os.environ.setdefault("MCMANAGER_DATA_DIR", tempfile.mkdtemp(prefix="mcmanager-tests-"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mcmanager.settings")

# pytest-django's `pytest_load_initial_conftests` hook reads the
# ini-declared DJANGO_SETTINGS_MODULE and force-imports `mcmanager.settings`
# before this conftest module's body runs, so it picks up whatever
# MCMANAGER_DATA_DIR was already in the OS environment (or none at all)
# instead of the temp dir set above. Evict the stale import and reset
# Django's lazy settings wrapper so the next settings access re-imports
# mcmanager.settings and re-reads MCMANAGER_DATA_DIR correctly. Verified
# empirically with pytest 9.1.1 / pytest-django 4.12.0: without this,
# settings.USER_DATA_DIR resolves to the real ~/.mcmanager, not the temp dir.
if "mcmanager.settings" in sys.modules:
    del sys.modules["mcmanager.settings"]
    from django.conf import settings
    from django.utils.functional import empty
    settings._wrapped = empty

# The Python 3.14 / Django 5.1 BaseContext.__copy__ compatibility shim that
# used to live here now lives in `mcmanager/console/compat.py`, applied by
# `ConsoleConfig.ready()` (see its docstring for the full explanation) — it
# was originally patched here because it only affected the test Client, but
# the same bug turned out to break real (non-test) requests too, such as
# every Django admin change-list page, so it needed to apply at Django
# startup in general, not just under pytest. `ready()` already runs during
# `pytest_load_initial_conftests` (before this file's body executes), so no
# duplicate patch is needed here anymore.
#
# `ConsoleConfig.ready()` itself no longer has a DB-touching side effect
# (removed in Phase 2 along with the `Server.status` cache field), so unlike
# the env-var fix above, there's nothing here left to work around for it.
