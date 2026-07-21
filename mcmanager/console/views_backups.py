from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from .models import Backup, Server
from .services import backups


@staff_member_required
def list_backups_view(request, server_id):
    server = Server.objects.get(id=server_id)
    return JsonResponse({'status': 'success', 'backups': backups.list_backups(server)})


@staff_member_required
@require_POST
def start_backup_view(request, server_id):
    server = Server.objects.get(id=server_id)
    backup = backups.start_backup(server)
    return JsonResponse({'status': 'success', 'backup_id': backup.id})


@staff_member_required
def backup_status_view(request, backup_id):
    try:
        backup = Backup.objects.get(id=backup_id)
    except Backup.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'Backup not found'})
    return JsonResponse({
        'status': 'success',
        'backup_status': backup.status,
        'error_message': backup.error_message,
        'filename': backup.filename,
    })


@staff_member_required
@require_POST
def restore_backup_view(request, server_id):
    server = Server.objects.get(id=server_id)
    filename = request.POST.get('filename')
    if not filename:
        return JsonResponse({'status': 'error', 'message': 'No backup filename provided'})
    try:
        backups.start_restore(server, filename)
    except Exception as exc:
        return JsonResponse({'status': 'error', 'message': str(exc)})
    return JsonResponse({'status': 'success', 'message': 'Backup restored'})


@staff_member_required
@require_POST
def delete_backup_view(request, server_id):
    server = Server.objects.get(id=server_id)
    filename = request.POST.get('filename')
    if not filename:
        return JsonResponse({'status': 'error', 'message': 'No backup filename provided'})
    try:
        backups.delete_backup(server, filename)
    except Exception as exc:
        return JsonResponse({'status': 'error', 'message': str(exc)})
    return JsonResponse({'status': 'success'})
