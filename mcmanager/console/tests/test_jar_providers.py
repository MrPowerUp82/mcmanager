import json
from unittest.mock import patch, MagicMock

import pytest

from mcmanager.console.services.jar_providers import mojang, paper

MOJANG_MANIFEST = {
    "versions": [
        {"id": "24w10a", "type": "snapshot", "url": "https://example.com/24w10a.json"},
        {"id": "1.20.4", "type": "release", "url": "https://example.com/1.20.4.json"},
        {"id": "1.20.3", "type": "release", "url": "https://example.com/1.20.3.json"},
        {"id": "1.19.4", "type": "old_beta", "url": "https://example.com/1.19.4.json"},
    ]
}

MOJANG_VERSION_DETAIL = {
    "downloads": {
        "server": {
            "url": "https://example.com/server-1.20.4.jar",
            "sha1": "abc123def456",
        }
    }
}

PAPER_VERSIONS = {"versions": ["1.20.3", "1.20.4"]}

PAPER_BUILDS = {
    "builds": [
        {
            "build": 450,
            "downloads": {"application": {"name": "paper-1.20.4-450.jar", "sha256": "old-hash"}},
        },
        {
            "build": 451,
            "downloads": {"application": {"name": "paper-1.20.4-451.jar", "sha256": "new-hash"}},
        },
    ]
}


def _mock_urlopen_returning(payload):
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(payload).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp
    return mock_resp


def test_mojang_list_versions_filters_to_releases_most_recent_first():
    with patch("urllib.request.urlopen", return_value=_mock_urlopen_returning(MOJANG_MANIFEST)):
        versions = mojang.list_versions()

    assert [v.version for v in versions] == ["1.20.4", "1.20.3"]
    assert all(v.label == v.version for v in versions)


def test_mojang_get_download_info_returns_server_jar_url_and_sha1():
    responses = [_mock_urlopen_returning(MOJANG_MANIFEST), _mock_urlopen_returning(MOJANG_VERSION_DETAIL)]
    with patch("urllib.request.urlopen", side_effect=responses):
        info = mojang.get_download_info("1.20.4")

    assert info.url == "https://example.com/server-1.20.4.jar"
    assert info.expected_hash == "abc123def456"
    assert info.hash_algorithm == "sha1"
    assert info.filename == "vanilla-1.20.4.jar"


def test_mojang_get_download_info_raises_for_unknown_version():
    with patch("urllib.request.urlopen", return_value=_mock_urlopen_returning(MOJANG_MANIFEST)):
        with pytest.raises(ValueError):
            mojang.get_download_info("99.99.99")


def test_paper_list_versions_returns_all_supported_versions():
    with patch("urllib.request.urlopen", return_value=_mock_urlopen_returning(PAPER_VERSIONS)):
        versions = paper.list_versions()

    assert [v.version for v in versions] == ["1.20.3", "1.20.4"]


def test_paper_get_download_info_picks_highest_build_number():
    with patch("urllib.request.urlopen", return_value=_mock_urlopen_returning(PAPER_BUILDS)):
        info = paper.get_download_info("1.20.4")

    assert info.filename == "paper-1.20.4-451.jar"
    assert info.expected_hash == "new-hash"
    assert info.hash_algorithm == "sha256"
