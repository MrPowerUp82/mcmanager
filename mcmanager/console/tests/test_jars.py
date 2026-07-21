import hashlib
import time
from unittest.mock import patch

import pytest

from mcmanager.console.models import JarDownload
from mcmanager.console.services import jars
from mcmanager.console.services.jar_providers.base import DownloadInfo


def _wait_for_terminal_status(download_id, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        download = JarDownload.objects.get(id=download_id)
        if download.status in ('done', 'error'):
            return download
        time.sleep(0.05)
    raise AssertionError('Download did not reach a terminal status in time')


@pytest.mark.django_db(transaction=True)
def test_start_download_succeeds_with_matching_hash(settings, tmp_path):
    settings.JAR_DIR = tmp_path / 'jar'
    settings.JAR_DIR.mkdir()

    source_file = tmp_path / 'source.jar'
    content = b'fake jar bytes for testing'
    source_file.write_bytes(content)
    expected_hash = hashlib.sha256(content).hexdigest()

    fake_info = DownloadInfo(
        url=source_file.as_uri(),
        filename='test-server.jar',
        expected_hash=expected_hash,
        hash_algorithm='sha256',
    )
    with patch.object(jars.PROVIDERS['mojang'], 'get_download_info', return_value=fake_info):
        download = jars.start_download('mojang', '1.20.4')
        result = _wait_for_terminal_status(download.id)

    assert result.status == 'done'
    assert result.filename == 'test-server.jar'
    downloaded_path = settings.JAR_DIR / 'test-server.jar'
    assert downloaded_path.exists()
    assert downloaded_path.read_bytes() == content
    assert not (settings.JAR_DIR / 'test-server.jar.part').exists()


@pytest.mark.django_db(transaction=True)
def test_start_download_fails_and_cleans_up_on_hash_mismatch(settings, tmp_path):
    settings.JAR_DIR = tmp_path / 'jar'
    settings.JAR_DIR.mkdir()

    source_file = tmp_path / 'source.jar'
    source_file.write_bytes(b'fake jar bytes for testing')

    fake_info = DownloadInfo(
        url=source_file.as_uri(),
        filename='bad-server.jar',
        expected_hash='0000000000000000000000000000000000000000000000000000000000000000',
        hash_algorithm='sha256',
    )
    with patch.object(jars.PROVIDERS['mojang'], 'get_download_info', return_value=fake_info):
        download = jars.start_download('mojang', '1.20.4')
        result = _wait_for_terminal_status(download.id)

    assert result.status == 'error'
    assert 'hash' in result.error_message.lower() or 'checksum' in result.error_message.lower()
    assert not (settings.JAR_DIR / 'bad-server.jar').exists()
    assert not (settings.JAR_DIR / 'bad-server.jar.part').exists()


@pytest.mark.django_db(transaction=True)
def test_start_download_records_error_when_provider_raises(settings, tmp_path):
    settings.JAR_DIR = tmp_path / 'jar'
    settings.JAR_DIR.mkdir()

    with patch.object(jars.PROVIDERS['mojang'], 'get_download_info', side_effect=ValueError('Unknown version')):
        download = jars.start_download('mojang', 'not-a-real-version')
        result = _wait_for_terminal_status(download.id)

    assert result.status == 'error'
    assert 'Unknown version' in result.error_message


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    'unsafe_filename',
    ['../evil.jar', 'subdir/evil.jar', '..\\evil.jar', '..', '.'],
)
def test_start_download_rejects_unsafe_provider_filename(settings, tmp_path, unsafe_filename):
    settings.JAR_DIR = tmp_path / 'jar'
    settings.JAR_DIR.mkdir()

    source_file = tmp_path / 'source.jar'
    content = b'fake jar bytes for testing'
    source_file.write_bytes(content)
    expected_hash = hashlib.sha256(content).hexdigest()

    fake_info = DownloadInfo(
        url=source_file.as_uri(),
        filename=unsafe_filename,
        expected_hash=expected_hash,
        hash_algorithm='sha256',
    )
    with patch.object(jars.PROVIDERS['paper'], 'get_download_info', return_value=fake_info):
        download = jars.start_download('paper', '1.20.4')
        result = _wait_for_terminal_status(download.id)

    assert result.status == 'error'
    assert 'filename' in result.error_message.lower()
    # Nothing should have been written anywhere, including outside JAR_DIR.
    assert set(tmp_path.iterdir()) == {source_file, settings.JAR_DIR}
    assert list(settings.JAR_DIR.iterdir()) == []


@pytest.mark.django_db(transaction=True)
def test_downloaded_jar_becomes_visible_via_get_jar_files(settings, tmp_path):
    from mcmanager.console.models import get_jar_files

    settings.JAR_DIR = tmp_path / 'jar'
    settings.JAR_DIR.mkdir()

    source_file = tmp_path / 'source.jar'
    content = b'fake jar bytes for testing'
    source_file.write_bytes(content)
    expected_hash = hashlib.sha256(content).hexdigest()

    fake_info = DownloadInfo(
        url=source_file.as_uri(),
        filename='visible-server.jar',
        expected_hash=expected_hash,
        hash_algorithm='sha256',
    )
    with patch.object(jars.PROVIDERS['mojang'], 'get_download_info', return_value=fake_info):
        download = jars.start_download('mojang', '1.20.4')
        result = _wait_for_terminal_status(download.id)

    assert result.status == 'done'
    jar_files = get_jar_files()
    assert ('visible-server.jar', 'visible-server.jar') in jar_files


@pytest.mark.django_db
def test_list_versions_delegates_to_the_named_provider():
    from mcmanager.console.services.jar_providers.base import VersionInfo

    with patch.object(jars.PROVIDERS['paper'], 'list_versions', return_value=[VersionInfo('1.20.4', '1.20.4')]):
        versions = jars.list_versions('paper')

    assert versions == [VersionInfo('1.20.4', '1.20.4')]
