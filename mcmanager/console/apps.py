from django.apps import AppConfig


class ConsoleConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'mcmanager.console'

    def ready(self):
        from .compat import patch_context_copy_for_python314
        patch_context_copy_for_python314()
