import json
import urllib.request

from .base import DownloadInfo, VersionInfo

VERSION_MANIFEST_URL = 'https://piston-meta.mojang.com/mc/game/version_manifest_v2.json'


def _fetch_json(url):
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read())


def list_versions():
    manifest = _fetch_json(VERSION_MANIFEST_URL)
    return [
        VersionInfo(version=v['id'], label=v['id'])
        for v in manifest['versions']
        if v['type'] == 'release'
    ]


def get_download_info(version):
    manifest = _fetch_json(VERSION_MANIFEST_URL)
    entry = next((v for v in manifest['versions'] if v['id'] == version), None)
    if entry is None:
        raise ValueError(f'Unknown Mojang version: {version}')

    version_data = _fetch_json(entry['url'])
    server_download = version_data['downloads']['server']
    return DownloadInfo(
        url=server_download['url'],
        filename=f'vanilla-{version}.jar',
        expected_hash=server_download['sha1'],
        hash_algorithm='sha1',
    )
