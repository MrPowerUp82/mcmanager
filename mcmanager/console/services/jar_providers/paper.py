import json
import urllib.request

from .base import DownloadInfo, VersionInfo

PROJECT_URL = 'https://api.papermc.io/v2/projects/paper'


def _fetch_json(url):
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read())


def list_versions():
    data = _fetch_json(PROJECT_URL)
    return [VersionInfo(version=v, label=v) for v in data['versions']]


def get_download_info(version):
    builds_data = _fetch_json(f'{PROJECT_URL}/versions/{version}/builds')
    builds = builds_data['builds']
    if not builds:
        raise ValueError(f'No PaperMC builds found for version: {version}')

    latest_build = max(builds, key=lambda b: b['build'])
    application = latest_build['downloads']['application']
    return DownloadInfo(
        url=f'{PROJECT_URL}/versions/{version}/builds/{latest_build["build"]}/downloads/{application["name"]}',
        filename=application['name'],
        expected_hash=application['sha256'],
        hash_algorithm='sha256',
    )
