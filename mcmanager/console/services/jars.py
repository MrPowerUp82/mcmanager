"""Orchestrates jar downloads: resolves a version to a download URL+hash via
the chosen provider, streams the file to JAR_DIR, verifies the checksum, and
only makes the file visible (via an atomic rename) once it's verified."""
import hashlib
import threading
import urllib.request

from django.conf import settings

from ..models import JarDownload
from .jar_providers import mojang, paper

PROVIDERS = {'mojang': mojang, 'paper': paper}


def list_versions(provider_name):
    return PROVIDERS[provider_name].list_versions()


def start_download(provider_name, version):
    download = JarDownload.objects.create(provider=provider_name, version=version, status='pending')
    thread = threading.Thread(target=_run_download, args=(download.id,), daemon=True)
    thread.start()
    return download


def _run_download(download_id):
    download = JarDownload.objects.get(id=download_id)
    download.status = 'downloading'
    download.save(update_fields=['status'])

    tmp_path = None
    try:
        info = PROVIDERS[download.provider].get_download_info(download.version)

        if not info.filename or info.filename in ('.', '..') or '/' in info.filename or '\\' in info.filename:
            download.status = 'error'
            download.error_message = (
                f'Filename safety violation -- provider returned an unsafe filename: {info.filename!r}'
            )
            download.save(update_fields=['status', 'error_message'])
            return

        dest_path = settings.JAR_DIR / info.filename
        tmp_path = dest_path.with_suffix(dest_path.suffix + '.part')
        hasher = hashlib.new(info.hash_algorithm)

        with urllib.request.urlopen(info.url, timeout=30) as resp, open(tmp_path, 'wb') as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                hasher.update(chunk)

        if hasher.hexdigest() != info.expected_hash:
            tmp_path.unlink(missing_ok=True)
            download.status = 'error'
            download.error_message = 'Hash mismatch -- downloaded file did not match expected checksum'
            download.save(update_fields=['status', 'error_message'])
            return

        tmp_path.rename(dest_path)
        download.filename = info.filename
        download.status = 'done'
        download.save(update_fields=['filename', 'status'])
    except Exception as exc:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        download.status = 'error'
        download.error_message = str(exc)
        download.save(update_fields=['status', 'error_message'])
