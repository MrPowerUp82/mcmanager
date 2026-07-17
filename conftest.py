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

# Known residual gap: `mcmanager.console.apps.ConsoleConfig.ready()` runs a
# DB query/save loop as a side effect of Django app-registry population,
# which pytest-django triggers inside `pytest_load_initial_conftests` — i.e.
# before this file's env-var fix above has any chance to run. That one-time
# `ready()` call still reads/writes whatever `MCMANAGER_DATA_DIR` resolves to
# at that point (the real ~/.mcmanager if unset in the OS environment), not
# the isolated temp dir. No fix is possible from conftest.py alone (confirmed
# by removing the ini-declared DJANGO_SETTINGS_MODULE, which broke Django app
# loading entirely instead). Fixing this requires changing what
# ConsoleConfig.ready() does, tracked separately — out of scope here.
