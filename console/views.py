import subprocess
import os, pty, psutil
from django.http import JsonResponse, HttpRequest
from django.shortcuts import render, redirect
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from .models import Server

LOG_FILE = 'logs/latest.log'

def is_server_running_v2(id):
    server = Server.objects.get(id=id)
    result = os.popen(f"pgrep -f {server.jar}").read().strip()
    if result:
        print("PID: ", result)
        return True
    return False

def is_server_running(id):
    SERVER_PID_FILE = f'/tmp/minecraft_server_{id}.pid'
    if os.path.exists(SERVER_PID_FILE):
        with open(SERVER_PID_FILE, 'r') as f:
            pid = int(f.read().strip())
            try:
                os.kill(pid, 0)
                print("PID: ", pid)
                return True
            except OSError:
                pass
    return False

@staff_member_required
def index(request, id):
    server = Server.objects.get(id=id)
    server_running = is_server_running(id)
    return render(request, 'console/index.html', {'server_running': server_running, 'server': server})

def start_server(request, id):
    server = Server.objects.get(id=id)
    SERVER_COMMAND = f"{settings.JAVA_BIN_PATH} -Xmx{server.memory_limit}M -jar {server.jar} --nogui"
    SERVER_DIRECTORY = os.path.join(settings.BASE_DIR, 'servers', f'server_{server.id}')
    SERVER_PID_FILE = f'/tmp/minecraft_server_{id}.pid'
    SERVER_PTY_FILE = f'/tmp/minecraft_server_{id}.pty'
    if not is_server_running(id):
        os.chdir(SERVER_DIRECTORY)
        pid, fd = pty.fork()
        if pid == 0:
            os.execv('/bin/sh', ['/bin/sh', '-c', SERVER_COMMAND])
        with open(SERVER_PID_FILE, 'w') as f:
            f.write(str(pid))
        with open(SERVER_PTY_FILE, 'w') as f:
            f.write(str(fd))
        server.status = True
        server.save()
        return JsonResponse({'status': 'success', 'message': 'Server started'})
    else:
        return JsonResponse({'status': 'error', 'message': 'Server is already running'})

def force_stop_server(request, id):
    server = Server.objects.get(id=id)
    SERVER_PID_FILE = f'/tmp/minecraft_server_{id}.pid'
    SERVER_PTY_FILE = f'/tmp/minecraft_server_{id}.pty'
    try:
        os.popen(f"pkill -f {server.jar}")
        try:
            os.remove(SERVER_PID_FILE)
            os.remove(SERVER_PTY_FILE)
        except FileNotFoundError:
            pass
        server.status = False
        server.save()
        return JsonResponse({'status': 'success', 'message': 'Server stopped'})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)})

def stop_server(request, id):
    server = Server.objects.get(id=id)
    SERVER_PID_FILE = f'/tmp/minecraft_server_{id}.pid'
    SERVER_PTY_FILE = f'/tmp/minecraft_server_{id}.pty'
    if is_server_running(id):
        with open(SERVER_PID_FILE, 'r') as f:
            pid = int(f.read().strip())
            os.kill(pid, 15)  # Enviar sinal SIGTERM
        os.remove(SERVER_PID_FILE)
        os.remove(SERVER_PTY_FILE)
        server.status = False
        server.save()
        return JsonResponse({'status': 'success', 'message': 'Server stopped'})
    else:
        return JsonResponse({'status': 'error', 'message': 'Server is not running'})

def view_logs(request, id):
    server = Server.objects.get(id=id)
    SERVER_DIRECTORY = os.path.join(settings.BASE_DIR, 'servers', f'server_{server.id}')
    if os.path.exists(os.path.join(SERVER_DIRECTORY, LOG_FILE)):
        with open(os.path.join(SERVER_DIRECTORY, LOG_FILE), 'r') as f:
            logs = f.read()
        return JsonResponse({'status': 'success', 'logs': logs})
    else:
        return JsonResponse({'status': 'error', 'message': 'Log file not found'})

@csrf_exempt
@staff_member_required
def send_command(request, id):
    SERVER_PTY_FILE = f'/tmp/minecraft_server_{id}.pty'
    if request.method == 'POST':
        command = request.POST.get('command')
        if is_server_running(id):
            try:
                with open(SERVER_PTY_FILE, 'r') as f:
                    fd = int(f.read().strip())
                    os.write(fd, f'{command}\n'.encode())
                    return JsonResponse({'status': 'success', 'message': 'Command sent'})
            except OSError as e:
                return JsonResponse({'status': 'error', 'message': str(e)})
        return JsonResponse({'status': 'error', 'message': 'Server is not running'})
    return JsonResponse({'status': 'failed', 'message': 'Invalid request method'})

def get_server_stats_v2(request, id):
    SERVER_PID_FILE = f'/tmp/minecraft_server_{id}.pid'
    if is_server_running(id):
        try:
            with open(SERVER_PID_FILE, 'r') as f:
                pid = int(f.read().strip())
                
            # Comando para obter uso de CPU do processo
            cpu_command = f"ps -p {pid} -o %cpu"
            cpu_usage = subprocess.check_output(cpu_command, shell=True).decode('utf-8').split('\n')[1].strip()

            # Comando para obter uso de memória do processo
            mem_command = f"ps -p {pid} -o rss"
            mem_usage = subprocess.check_output(mem_command, shell=True).decode('utf-8').split('\n')[1].strip()
            mem_usage = int(mem_usage) / 1024  # Convertendo de KB para MB

            # Comando para obter uso de memória total e usada do sistema
            mem_total_command = "free -m | awk '/Mem:/ {print $2}'"
            mem_total = subprocess.check_output(mem_total_command, shell=True).decode('utf-8').strip()

            mem_used_command = "free -m | awk '/Mem:/ {print $3}'"
            mem_used = subprocess.check_output(mem_used_command, shell=True).decode('utf-8').strip()

            return JsonResponse({
                'status': 'success',
                'cpu_usage': cpu_usage,
                'memory_usage': mem_usage,
                'total_memory': mem_total,
                'used_memory': mem_used,
            })
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)})
    return JsonResponse({'status': 'error', 'message': 'Server is not running'})

def get_server_stats(request, id):
    SERVER_PID_FILE = f'/tmp/minecraft_server_{id}.pid'
    if is_server_running(id):
        with open(SERVER_PID_FILE, 'r') as f:
            pid = int(f.read().strip())
            try:
                process = psutil.Process(pid)
            
                # Vamos coletar o uso de CPU em um intervalo mais longo para maior precisão
                cpu_usage = process.cpu_percent(interval=1)
                memory_info = process.memory_info()
                memory_usage = memory_info.rss / (1024 * 1024)  # Convertendo para MB

                # Coletando também o uso de memória virtual
                virtual_memory = psutil.virtual_memory()
                total_memory = virtual_memory.total / (1024 * 1024)  # Convertendo para MB
                used_memory = virtual_memory.used / (1024 * 1024)  # Convertendo para MB

                return JsonResponse({
                    'status': 'success',
                    'cpu_usage': cpu_usage,
                    'memory_usage': memory_usage,
                    'total_memory': total_memory,
                    'used_memory': used_memory,
                })
            except psutil.NoSuchProcess:
                return JsonResponse({'status': 'error', 'message': 'Process not found'})
    return JsonResponse({'status': 'error', 'message': 'Server is not running'})

def home(request: HttpRequest):
    ctx = {
        "servers": Server.objects.all()
    }
    if request.method == 'POST':
        server_id = request.POST.get('server_id')
        return redirect('index', id=server_id)
    return render(request, 'index.html', ctx)