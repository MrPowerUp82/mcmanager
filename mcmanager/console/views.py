from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpRequest, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from .json_utils import json_error
from .models import Server
from .services import dashboard, process, rcon

LOG_FILE = 'logs/latest.log'


@staff_member_required
def index(request, id):
    server = Server.objects.get(id=id)
    server_running = process.is_running(server)
    return render(request, 'console/index.html', {'server_running': server_running, 'server': server})


@staff_member_required
@require_POST
def start_server(request, id):
    server = Server.objects.get(id=id)
    try:
        process.start(server)
        server.desired_running = True
        server.consecutive_restart_failures = 0
        server.save(update_fields=['desired_running', 'consecutive_restart_failures'])
        return JsonResponse({'status': 'success', 'message': 'Server started'})
    except process.AlreadyRunningError:
        return json_error('Server is already running', status=409)
    except process.JavaNotFoundError as e:
        return json_error(str(e), status=503)
    except process.PortInUseError as e:
        return json_error(str(e), status=409)
    except process.JarMissingError as e:
        return json_error(str(e), status=409)


@staff_member_required
@require_POST
def force_stop_server(request, id):
    server = Server.objects.get(id=id)
    process.force_stop(server)
    server.desired_running = False
    server.save(update_fields=['desired_running'])
    return JsonResponse({'status': 'success', 'message': 'Server stopped'})


@staff_member_required
@require_POST
def stop_server(request, id):
    server = Server.objects.get(id=id)
    try:
        process.stop(server)
        server.desired_running = False
        server.save(update_fields=['desired_running'])
        return JsonResponse({'status': 'success', 'message': 'Server stopped'})
    except process.ProcessNotRunningError:
        return json_error('Server is not running', status=409)
    except process.StopTimeoutError as e:
        return json_error(str(e), status=504)
    except rcon.RconError as e:
        return json_error(str(e), status=502)


@staff_member_required
def view_logs(request, id):
    server = Server.objects.get(id=id)
    log_path = settings.SERVERS_DIR / f'server_{server.id}' / LOG_FILE
    try:
        offset = int(request.GET.get('offset', 0))
    except ValueError:
        offset = 0
    if offset < 0:
        offset = 0

    if not log_path.exists():
        return json_error('Log file not found', status=404)

    file_size = log_path.stat().st_size
    if offset > file_size:
        offset = 0

    with log_path.open('rb') as f:
        f.seek(offset)
        new_bytes = f.read()

    return JsonResponse({
        'status': 'success',
        'logs': new_bytes.decode('utf-8', errors='replace'),
        'offset': offset + len(new_bytes),
    })


@staff_member_required
@require_POST
def send_command(request, id):
    server = Server.objects.get(id=id)
    command = request.POST.get('command')
    if not command:
        return json_error('No command provided', status=400)
    try:
        response = process.send_command(server, command)
        return JsonResponse({'status': 'success', 'message': response or 'Command sent'})
    except process.ProcessNotRunningError:
        return json_error('Server is not running', status=409)
    except rcon.RconError as e:
        return json_error(str(e), status=502)


@staff_member_required
def get_server_stats(request, id):
    server = Server.objects.get(id=id)
    try:
        stats = process.get_stats(server)
        return JsonResponse({'status': 'success', **stats})
    except process.ProcessNotRunningError:
        return json_error('Server is not running', status=409)


def _serialize_dashboard_entries(entries):
    return [
        {
            'id': entry['server'].id,
            'name': entry['server'].name,
            'running': entry['running'],
            'jar_missing': entry.get('jar_missing', False),
            'stats_available': entry.get('stats_available', False),
            'cpu_usage': entry.get('cpu_usage'),
            'memory_usage': entry.get('memory_usage'),
            'players_available': entry.get('players_available', False),
            'players_raw': entry.get('players_raw'),
        }
        for entry in entries
    ]


@staff_member_required
def home(request: HttpRequest):
    servers = _serialize_dashboard_entries(dashboard.get_dashboard_data())
    return render(request, 'index.html', {'initial_servers': servers})


@staff_member_required
def dashboard_data(request: HttpRequest):
    entries = dashboard.get_dashboard_data()
    return JsonResponse({'status': 'success', 'servers': _serialize_dashboard_entries(entries)})
