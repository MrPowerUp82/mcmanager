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

# Python 3.14 compatibility shim: Django 5.1's BaseContext.__copy__
# (django/template/context.py) does `duplicate = copy(super())` to get a
# shallow copy of self while bypassing dict's own __copy__ (BaseContext
# isn't a dict subclass, but relies on this idiom to allocate a bare
# instance). Python 3.14 changed `copy.copy()`'s handling of `super`
# proxy objects, so this now raises
# `AttributeError: 'super' object has no attribute 'dicts' and no
# __dict__ for setting new attributes` any time Django renders a
# Template while the test Client's `template_rendered` signal
# instrumentation is active (django.test.utils.instrumented_test_render
# copies the Context for every template render during a test-client
# request). Reproduced independently of Django with a minimal
# `copy(super())` snippet, so this is an upstream Python 3.14 regression,
# not an application bug. Django 5.1.15 (latest 5.1.x as of writing) does
# not include a fix; only affects tests that cause a template to render
# through the Django test Client (e.g. CSRF-failure pages, any 200 HTML
# response). Patch __copy__ to a version that doesn't rely on the broken
# idiom.
# TODO: remove once either (a) Django ships a fix for the copy(super())
# idiom under Python 3.14+, or (b) this project's supported Python floor
# is raised past whatever version fixes it — check `python --version` and
# retest by deleting this block before removing it.
from django.template.context import BaseContext  # noqa: E402


def _base_context_copy(self):
    duplicate = object.__new__(self.__class__)
    duplicate.__dict__.update(self.__dict__)
    duplicate.dicts = self.dicts[:]
    return duplicate


BaseContext.__copy__ = _base_context_copy

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
