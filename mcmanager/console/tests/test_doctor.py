from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from mcmanager.console.services import doctor


def test_check_java_passes_and_parses_version(settings):
    settings.JAVA_BIN_PATH = '/fake/java'
    fake_result = MagicMock(returncode=0, stdout='', stderr='openjdk version "17.0.9" 2023-10-17\n')
    with patch('mcmanager.console.services.doctor.subprocess.run', return_value=fake_result):
        result = doctor.check_java()
    assert result['name'] == 'Java'
    assert result['passed'] is True
    assert '17.0.9' in result['message']


def test_check_java_fails_when_binary_missing(settings):
    settings.JAVA_BIN_PATH = '/fake/java'
    with patch('mcmanager.console.services.doctor.subprocess.run', side_effect=FileNotFoundError):
        result = doctor.check_java()
    assert result['passed'] is False
    assert '/fake/java' in result['message']


def test_check_java_passes_but_version_unparseable(settings):
    settings.JAVA_BIN_PATH = '/fake/java'
    fake_result = MagicMock(returncode=0, stdout='', stderr='some unexpected output\n')
    with patch('mcmanager.console.services.doctor.subprocess.run', return_value=fake_result):
        result = doctor.check_java()
    assert result['passed'] is True
    assert 'não identificada' in result['message']


def test_check_java_fails_when_nonzero_exit(settings):
    settings.JAVA_BIN_PATH = '/fake/java'
    fake_result = MagicMock(returncode=1, stdout='', stderr='error\n')
    with patch('mcmanager.console.services.doctor.subprocess.run', return_value=fake_result):
        result = doctor.check_java()
    assert result['passed'] is False


@pytest.mark.django_db
def test_check_data_directories_passes_when_all_writable(settings, tmp_path):
    for label in ['JAR_DIR', 'SERVERS_DIR', 'CONFIGS_DIR', 'RUN_DIR', 'BACKUPS_DIR']:
        directory = tmp_path / label.lower()
        directory.mkdir()
        setattr(settings, label, directory)

    result = doctor.check_data_directories()

    assert result['name'] == 'Diretórios de dados'
    assert result['passed'] is True


@pytest.mark.django_db
def test_check_data_directories_fails_when_one_unwritable(settings, tmp_path):
    for label in ['JAR_DIR', 'SERVERS_DIR', 'CONFIGS_DIR', 'RUN_DIR', 'BACKUPS_DIR']:
        directory = tmp_path / label.lower()
        directory.mkdir()
        setattr(settings, label, directory)

    def fake_access(path, mode):
        return str(path) != str(settings.RUN_DIR)

    with patch('mcmanager.console.services.doctor.os.access', side_effect=fake_access):
        result = doctor.check_data_directories()

    assert result['passed'] is False
    assert 'RUN_DIR' in result['message']


@pytest.mark.django_db
def test_check_migrations_passes_when_up_to_date():
    result = doctor.check_migrations()
    assert result['name'] == 'Migrações'
    assert result['passed'] is True


@pytest.mark.django_db
def test_check_migrations_fails_when_pending():
    fake_migration = SimpleNamespace(app_label='console', name='0011_fake')
    with patch('mcmanager.console.services.doctor.MigrationExecutor') as mock_executor_cls:
        mock_executor = mock_executor_cls.return_value
        mock_executor.loader.graph.leaf_nodes.return_value = []
        mock_executor.migration_plan.return_value = [(fake_migration, False)]
        result = doctor.check_migrations()

    assert result['passed'] is False
    assert 'console.0011_fake' in result['message']


@pytest.mark.django_db
def test_run_checks_returns_all_three_checks_in_order():
    results = doctor.run_checks()
    assert [r['name'] for r in results] == ['Java', 'Diretórios de dados', 'Migrações']
