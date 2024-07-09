import subprocess
import os, pty
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required

SERVER_COMMAND = "/usr/lib/jvm/java-8-openjdk-amd64/bin/java -Xms1024M -Xmx1500M -jar custom.jar --nogui --port 25566"
SERVER_DIRECTORY = os.path.join(settings.BASE_DIR, 'server')
LOG_FILE = 'logs/latest.log'
SERVER_PID_FILE = '/tmp/minecraft_server.pid'
SERVER_PTY_FILE = '/tmp/minecraft_server.pty'

def is_server_running():
    if os.path.exists(SERVER_PID_FILE):
        with open(SERVER_PID_FILE, 'r') as f:
            pid = int(f.read().strip())
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                pass
    return False

@staff_member_required
def index(request):
    server_running = is_server_running()
    return render(request, 'console/index.html', {'server_running': server_running})

def start_server(request):
    if not is_server_running():
        os.chdir(SERVER_DIRECTORY)
        pid, fd = pty.fork()
        if pid == 0:
            os.execv('/bin/sh', ['/bin/sh', '-c', SERVER_COMMAND])
        with open(SERVER_PID_FILE, 'w') as f:
            f.write(str(pid))
        with open(SERVER_PTY_FILE, 'w') as f:
            f.write(str(fd))
        return JsonResponse({'status': 'success', 'message': 'Server started'})
    else:
        return JsonResponse({'status': 'error', 'message': 'Server is already running'})

def force_stop_server(request):
    try:
        os.popen("pkill -f custom.jar")
        try:
            os.remove(SERVER_PID_FILE)
            os.remove(SERVER_PTY_FILE)
        except FileNotFoundError:
            pass
        return JsonResponse({'status': 'success', 'message': 'Server stopped'})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)})

def stop_server(request):
    if is_server_running():
        with open(SERVER_PID_FILE, 'r') as f:
            pid = int(f.read().strip())
            os.kill(pid, 15)  # Enviar sinal SIGTERM
        os.remove(SERVER_PID_FILE)
        os.remove(SERVER_PTY_FILE)
        return JsonResponse({'status': 'success', 'message': 'Server stopped'})
    else:
        return JsonResponse({'status': 'error', 'message': 'Server is not running'})

def view_logs(request):
    if os.path.exists(os.path.join(SERVER_DIRECTORY, LOG_FILE)):
        with open(os.path.join(SERVER_DIRECTORY, LOG_FILE), 'r') as f:
            logs = f.read()
        return JsonResponse({'status': 'success', 'logs': logs})
    else:
        return JsonResponse({'status': 'error', 'message': 'Log file not found'})

@csrf_exempt
@staff_member_required
def send_command(request):
    if request.method == 'POST':
        command = request.POST.get('command')
        if is_server_running():
            try:
                with open(SERVER_PTY_FILE, 'r') as f:
                    fd = int(f.read().strip())
                    os.write(fd, f'{command}\n'.encode())
                    return JsonResponse({'status': 'success', 'message': 'Command sent'})
            except OSError as e:
                return JsonResponse({'status': 'error', 'message': str(e)})
        return JsonResponse({'status': 'error', 'message': 'Server is not running'})
    return JsonResponse({'status': 'failed', 'message': 'Invalid request method'})

