import shutil

from django.conf import settings


def create_server_files(server):
    server_path = settings.SERVERS_DIR / f'server_{server.id}'
    properties_path = server_path / 'server.properties'

    server.jar = f'{server.id}_{server.jar_template}'
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

    lines = properties_path.read_text(encoding='utf-8', errors='ignore').splitlines(keepends=True)
    new_lines = [
        f'server-port={server.port}\n' if line.startswith('server-port=') else line
        for line in lines
    ]
    properties_path.write_text(''.join(new_lines), encoding='utf-8')

    server.server_properties = properties_path.read_text(encoding='utf-8', errors='ignore')
    server.save(update_fields=['jar', 'server_properties'])


def sync_server_properties_file(server):
    server_path = settings.SERVERS_DIR / f'server_{server.id}'
    properties_path = server_path / 'server.properties'
    properties_path.write_text(server.server_properties or '', encoding='utf-8')


def delete_server_files(server):
    server_path = settings.SERVERS_DIR / f'server_{server.id}'
    if server_path.exists():
        shutil.rmtree(server_path)
