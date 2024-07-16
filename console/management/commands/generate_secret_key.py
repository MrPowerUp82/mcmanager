import os
import re
from django.core.management.base import BaseCommand
from django.core.management.utils import get_random_secret_key
from django.conf import settings


class Command(BaseCommand):
    """
    Gerar uma nova SECRET_KEY e atualizá-la no arquivo settings.py
    """
    help = 'Gerar uma nova SECRET_KEY e atualizá-la no arquivo settings.py'

    def handle(self, *args, **kwargs):
        secret_key = get_random_secret_key()
        for root, dirs, files in os.walk(settings.BASE_DIR):
            if 'settings.py' in files and 'asgi.py' in files and 'wsgi.py' in files:
                settings_path = os.path.join(root, 'settings.py')
                break
        with open(settings_path, 'r') as file:
            settings_content = file.read()
        new_settings_content = re.sub(
            r"^SECRET_KEY = ['\"].*['\"]",
            f"SECRET_KEY = '{secret_key}'",
            settings_content,
            flags=re.MULTILINE
        )
        with open(settings_path, 'w') as file:
            file.write(new_settings_content)
        self.stdout.write(self.style.SUCCESS(
            f'Nova SECRET_KEY gerada e atualizada no settings.py: {secret_key}'))
