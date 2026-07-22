from unittest.mock import MagicMock, patch

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
