import shutil
import string

from django.conf import settings
from django.utils.crypto import get_random_string

RCON_PORT_OFFSET = 10000
_RCON_PASSWORD_ALPHABET = string.ascii_letters + string.digits


def generate_rcon_credentials(server):
    """Returns (rcon_port, rcon_password) for `server`. Does not persist anything."""
    rcon_port = server.port + RCON_PORT_OFFSET
    rcon_password = get_random_string(24, _RCON_PASSWORD_ALPHABET)
    return rcon_port, rcon_password


def rewrite_properties(properties_path, updates):
    """Rewrites existing `key=value` lines in place; appends keys not already present.
    Used both for first-time provisioning and the Task 6 RCON-credential backfill migration."""
    lines = properties_path.read_text(encoding='utf-8', errors='ignore').splitlines(keepends=True)
    remaining = dict(updates)
    new_lines = []
    for line in lines:
        matched_key = next((k for k in remaining if line.startswith(f'{k}=')), None)
        if matched_key is not None:
            new_lines.append(f'{matched_key}={remaining.pop(matched_key)}\n')
        else:
            new_lines.append(line)
    for key, value in remaining.items():
        new_lines.append(f'{key}={value}\n')
    properties_path.write_text(''.join(new_lines), encoding='utf-8')


def create_server_files(server):
    server_path = settings.SERVERS_DIR / f'server_{server.id}'
    properties_path = server_path / 'server.properties'

    server.jar = f'{server.id}_{server.jar_template}'
    server.rcon_port, server.rcon_password = generate_rcon_credentials(server)
    server_path.mkdir(parents=True, exist_ok=True)
    shutil.copy(settings.JAR_DIR / server.jar_template, server_path / server.jar)
    shutil.copy(settings.CONFIGS_DIR / 'server.properties', properties_path)

    if server.type.dependencies:
        for dependency in server.type.dependencies:
            dep_source = settings.JAR_DIR / dependency
            dep_dest = server_path / dependency
            if dep_source.is_dir():
                shutil.copytree(dep_source, dep_dest)
            else:
                shutil.copy(dep_source, dep_dest)

    (server_path / 'eula.txt').write_text('eula=true', encoding='utf-8')

    rewrite_properties(properties_path, {
        'server-port': server.port,
        'enable-rcon': 'true',
        'rcon.port': server.rcon_port,
        'rcon.password': server.rcon_password,
        'rcon.bind': '127.0.0.1',
    })

    server.server_properties = properties_path.read_text(encoding='utf-8', errors='ignore')
    server.save(update_fields=['jar', 'rcon_port', 'rcon_password', 'server_properties'])


def sync_server_properties_file(server):
    server_path = settings.SERVERS_DIR / f'server_{server.id}'
    properties_path = server_path / 'server.properties'
    properties_path.write_text(server.server_properties or '', encoding='utf-8')


def delete_server_files(server):
    server_path = settings.SERVERS_DIR / f'server_{server.id}'
    if server_path.exists():
        shutil.rmtree(server_path)
