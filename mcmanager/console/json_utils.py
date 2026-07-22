"""Shared JSON error-response helper. Success responses keep their existing,
per-view flat shape unchanged — only error responses use this, so they get a
real HTTP status code instead of always returning 200."""
from django.http import JsonResponse


def json_error(message, status=400):
    return JsonResponse({'status': 'error', 'message': message}, status=status)
