from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from .models import JarDownload
from .services import jars

VALID_PROVIDERS = ('mojang', 'paper')


@staff_member_required
def jars_page(request):
    return render(request, 'console/jars.html', {})


@staff_member_required
def list_jar_versions(request, provider):
    if provider not in VALID_PROVIDERS:
        return JsonResponse({'status': 'error', 'message': f'Unknown provider: {provider}'})
    try:
        versions = jars.list_versions(provider)
    except Exception as exc:
        return JsonResponse({'status': 'error', 'message': str(exc)})
    return JsonResponse({
        'status': 'success',
        'versions': [{'version': v.version, 'label': v.label} for v in versions],
    })


@staff_member_required
@require_POST
def start_jar_download(request):
    provider = request.POST.get('provider')
    version = request.POST.get('version')
    if provider not in VALID_PROVIDERS or not version:
        return JsonResponse({'status': 'error', 'message': 'Invalid provider or version'})
    download = jars.start_download(provider, version)
    return JsonResponse({'status': 'success', 'download_id': download.id})


@staff_member_required
def jar_download_status(request, download_id):
    try:
        download = JarDownload.objects.get(id=download_id)
    except JarDownload.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'Download not found'})
    return JsonResponse({
        'status': 'success',
        'download_status': download.status,
        'error_message': download.error_message,
        'filename': download.filename,
    })
