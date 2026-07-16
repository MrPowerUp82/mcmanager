import os
from django.core.management.base import BaseCommand
from django.core.management.utils import get_random_secret_key
from django.conf import settings


class Command(BaseCommand):
    """
    Gerar uma nova SECRET_KEY e atualizá-la no arquivo .secret_key do diretório de dados
    """
    help = 'Gerar uma nova SECRET_KEY e atualizá-la no arquivo .secret_key do diretório de dados'

    def handle(self, *args, **kwargs):
        secret_key = get_random_secret_key()
        user_data_dir = getattr(settings, 'USER_DATA_DIR', settings.BASE_DIR)
        secret_key_file = user_data_dir / '.secret_key'
        
        user_data_dir.mkdir(parents=True, exist_ok=True)
        secret_key_file.write_text(secret_key, encoding='utf-8')
        
        self.stdout.write(self.style.SUCCESS(
            f'Nova SECRET_KEY gerada e salva com sucesso em: {secret_key_file}'
        ))

