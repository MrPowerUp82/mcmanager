"""Runtime compatibility shims for this project's supported environments."""
from django.template.context import BaseContext


def patch_context_copy_for_python314():
    """Python 3.14 changed copy.copy()'s handling of `super` proxy objects,
    breaking Django 5.1's BaseContext.__copy__ (which does
    `duplicate = copy(super())` to get a shallow copy of self while bypassing
    dict's own __copy__ -- BaseContext isn't a dict subclass, but relies on
    this idiom to allocate a bare instance). This now raises
    `AttributeError: 'super' object has no attribute 'dicts' and no __dict__
    for setting new attributes` any time Django copies a template Context --
    which happens on every Django admin change-list page render (via the
    `{% change_list_object_tools %}` templatetag), and more generally
    whenever `Context.new()` is called. Reproduced independently of Django
    with a minimal `copy(super())` snippet, so this is an upstream Python
    3.14 regression, not an application bug. Django 5.1.15 (latest 5.1.x as
    of writing) does not include a fix.

    TODO: remove once either (a) Django ships a fix for the copy(super())
    idiom under Python 3.14+, or (b) this project's supported Python floor
    is raised past whatever version fixes it -- check `python --version`
    and retest by removing this call before deleting it.
    """
    def _base_context_copy(self):
        duplicate = object.__new__(self.__class__)
        duplicate.__dict__.update(self.__dict__)
        duplicate.dicts = self.dicts[:]
        return duplicate

    BaseContext.__copy__ = _base_context_copy
