import subprocess
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """
    Formatar o código do projeto
    """
    help = 'Formatar o código do projeto'

    def handle(self, *args, **kwargs):
        # Executa o comando de formatação para *.py
        result1 = subprocess.getstatusoutput(
            "autopep8 --recursive --in-place --exclude venv,.git,node_modules ./")
        if result1[0] == 0:
            self.stdout.write(self.style.SUCCESS(
                'Código Python formatado com sucesso!'))
            if kwargs.get('verbosity', 1) > 1:
                self.stdout.write(self.style.SUCCESS(result1[1]))
        else:
            self.stdout.write(self.style.ERROR(
                'Erro ao formatar o código Python!'))
            if kwargs.get('verbosity', 1) > 1:
                self.stdout.write(self.style.ERROR(result1[1]))
        # Executa o comando de formatação para *.html, *.css e *.js
        result2 = subprocess.getstatusoutput(
            """find . -type d \\( -name 'venv' -o -name 'node_modules' -o -name '.git' \\) -prune -false -o -type d -name 'templates' | while read -r dir; do
    echo "Formating $dir"
    djlint "$dir" --reformat --format-css --format-js --use-gitignore
done""")
        if result2[0] == 0:
            self.stdout.write(self.style.SUCCESS(
                'Não foi necessário formatar o código HTML, CSS e JavaScript!'))
            if kwargs.get('verbosity', 1) > 1:
                self.stdout.write(self.style.SUCCESS(result2[1]))
        else:
            if kwargs.get('verbosity', 1) > 1:
                self.stdout.write(self.style.WARNING(
                    'Foram realizadas alterações no código HTML, CSS e JavaScript!'))
                self.stdout.write(self.style.WARNING(result2[1]))
            else:
                self.stdout.write(self.style.WARNING(
                    'Foram realizadas alterações no código HTML, CSS e JavaScript!\nUse "python manage.py format_code --verbosity 2" para mais detalhes.'))
