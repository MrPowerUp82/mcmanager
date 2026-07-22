"""Environment diagnostics for `mcmanager doctor`. Each check is a small,
independently testable function returning a dict; run_checks() aggregates
them so the CLI only needs to print and decide the exit code."""
import os
import re
import subprocess

from django.conf import settings
from django.db import connections
from django.db.migrations.executor import MigrationExecutor

JAVA_VERSION_PATTERN = re.compile(r'version "([^"]+)"')
JAVA_CHECK_TIMEOUT_SECONDS = 5


def check_java():
    java_path = settings.JAVA_BIN_PATH
    try:
        result = subprocess.run(
            [java_path, '-version'],
            capture_output=True,
            text=True,
            timeout=JAVA_CHECK_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return {'name': 'Java', 'passed': False, 'message': f'Java não encontrado em: {java_path}'}
    except OSError as exc:
        return {'name': 'Java', 'passed': False, 'message': f'Não foi possível executar Java em {java_path}: {exc}'}
    except subprocess.TimeoutExpired:
        return {
            'name': 'Java',
            'passed': False,
            'message': f'Java em {java_path} não respondeu em {JAVA_CHECK_TIMEOUT_SECONDS}s',
        }

    if result.returncode != 0:
        return {'name': 'Java', 'passed': False, 'message': f'Java em {java_path} retornou código {result.returncode}'}

    combined_output = result.stdout + result.stderr
    match = JAVA_VERSION_PATTERN.search(combined_output)
    if match:
        return {'name': 'Java', 'passed': True, 'message': f'Java {match.group(1)} encontrado em {java_path}'}
    return {'name': 'Java', 'passed': True, 'message': f'Java encontrado em {java_path} (versão não identificada)'}


DATA_DIRECTORIES = {
    'JAR_DIR': lambda: settings.JAR_DIR,
    'SERVERS_DIR': lambda: settings.SERVERS_DIR,
    'CONFIGS_DIR': lambda: settings.CONFIGS_DIR,
    'RUN_DIR': lambda: settings.RUN_DIR,
    'BACKUPS_DIR': lambda: settings.BACKUPS_DIR,
}


def check_data_directories():
    unwritable = []
    for label, get_path in DATA_DIRECTORIES.items():
        path = get_path()
        if not os.access(path, os.W_OK):
            unwritable.append(f'{label} ({path})')

    if unwritable:
        return {
            'name': 'Diretórios de dados',
            'passed': False,
            'message': f'Sem permissão de escrita em: {", ".join(unwritable)}',
        }
    return {'name': 'Diretórios de dados', 'passed': True, 'message': 'Todos os diretórios de dados são graváveis'}


def check_migrations():
    connection = connections['default']
    executor = MigrationExecutor(connection)
    plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    if plan:
        pending = ', '.join(f'{migration.app_label}.{migration.name}' for migration, _ in plan)
        return {'name': 'Migrações', 'passed': False, 'message': f'Migrações pendentes: {pending}'}
    return {'name': 'Migrações', 'passed': True, 'message': 'Banco de dados em dia'}


def run_checks():
    return [check_java(), check_data_directories(), check_migrations()]
