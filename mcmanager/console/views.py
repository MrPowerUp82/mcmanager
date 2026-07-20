from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpRequest, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .models import Server
from .services import process, rcon

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
        return JsonResponse({'status': 'success', 'message': 'Server started'})
    except process.AlreadyRunningError:
        return JsonResponse({'status': 'error', 'message': 'Server is already running'})
    except process.JavaNotFoundError as e:
        return JsonResponse({'status': 'error', 'message': str(e)})


@staff_member_required
@require_POST
def force_stop_server(request, id):
    server = Server.objects.get(id=id)
    process.force_stop(server)
    return JsonResponse({'status': 'success', 'message': 'Server stopped'})


@staff_member_required
@require_POST
def stop_server(request, id):
    server = Server.objects.get(id=id)
    try:
        process.stop(server)
        return JsonResponse({'status': 'success', 'message': 'Server stopped'})
    except process.ProcessNotRunningError:
        return JsonResponse({'status': 'error', 'message': 'Server is not running'})
    except process.StopTimeoutError as e:
        return JsonResponse({'status': 'error', 'message': str(e)})
    except rcon.RconError as e:
        return JsonResponse({'status': 'error', 'message': str(e)})


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
        return JsonResponse({'status': 'error', 'message': 'Log file not found'})

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
        return JsonResponse({'status': 'error', 'message': 'No command provided'})
    try:
        response = process.send_command(server, command)
        return JsonResponse({'status': 'success', 'message': response or 'Command sent'})
    except process.ProcessNotRunningError:
        return JsonResponse({'status': 'error', 'message': 'Server is not running'})
    except rcon.RconError as e:
        return JsonResponse({'status': 'error', 'message': str(e)})


@staff_member_required
def get_server_stats(request, id):
    server = Server.objects.get(id=id)
    try:
        stats = process.get_stats(server)
        return JsonResponse({'status': 'success', **stats})
    except process.ProcessNotRunningError:
        return JsonResponse({'status': 'error', 'message': 'Server is not running'})


@staff_member_required
def home(request: HttpRequest):
    if request.method == 'POST':
        server_id = request.POST.get('server_id')
        return redirect('index', id=server_id)
    ctx = {"servers": [(s, process.is_running(s)) for s in Server.objects.all()]}
    return render(request, 'index.html', ctx)
