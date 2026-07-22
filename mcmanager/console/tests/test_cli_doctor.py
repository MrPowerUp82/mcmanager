import sys
from unittest.mock import patch

import pytest

from mcmanager import cli


def test_doctor_command_exits_zero_when_all_checks_pass(capsys):
    fake_results = [
        {'name': 'Java', 'passed': True, 'message': 'Java 17.0.9 encontrado em /usr/bin/java'},
        {'name': 'Diretórios de dados', 'passed': True, 'message': 'Todos os diretórios de dados são graváveis'},
        {'name': 'Migrações', 'passed': True, 'message': 'Banco de dados em dia'},
    ]
    with patch.object(sys, 'argv', ['mcmanager', 'doctor']), \
         patch('mcmanager.console.services.doctor.run_checks', return_value=fake_results):
        with pytest.raises(SystemExit) as exc_info:
            cli.main()

    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert '[✓] Java: Java 17.0.9 encontrado em /usr/bin/java' in captured.out
    assert '[✓] Diretórios de dados' in captured.out
    assert '[✓] Migrações' in captured.out


def test_doctor_command_exits_one_when_a_check_fails(capsys):
    fake_results = [
        {'name': 'Java', 'passed': False, 'message': 'Java não encontrado em: /fake/java'},
        {'name': 'Diretórios de dados', 'passed': True, 'message': 'Todos os diretórios de dados são graváveis'},
        {'name': 'Migrações', 'passed': True, 'message': 'Banco de dados em dia'},
    ]
    with patch.object(sys, 'argv', ['mcmanager', 'doctor']), \
         patch('mcmanager.console.services.doctor.run_checks', return_value=fake_results):
        with pytest.raises(SystemExit) as exc_info:
            cli.main()

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert '[✗] Java: Java não encontrado em: /fake/java' in captured.out
