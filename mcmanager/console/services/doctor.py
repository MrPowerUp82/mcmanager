"""Environment diagnostics for `mcmanager doctor`. Each check is a small,
independently testable function returning a dict; run_checks() aggregates
them so the CLI only needs to print and decide the exit code."""
import re
import subprocess

from django.conf import settings

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
