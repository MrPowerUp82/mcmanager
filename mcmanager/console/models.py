import os

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


def get_jar_files():
    jar_dir = getattr(settings, 'JAR_DIR', os.path.join(settings.BASE_DIR, 'jar'))
    if not os.path.exists(jar_dir):
        os.makedirs(jar_dir)
    return [(x, x) for x in os.listdir(jar_dir) if x.endswith('.jar')]


def get_default_server_prop():
    configs_dir = getattr(settings, 'CONFIGS_DIR', os.path.join(settings.BASE_DIR, 'configs'))
    with open(os.path.join(configs_dir, 'server.properties'), 'r', encoding='utf-8') as arq:
        text = arq.read()
    return text


class Type(models.Model):
    name = models.CharField(max_length=100)
    dependencies = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


MAX_GAME_PORT = 55535  # port + 10000 (the RCON port) must not exceed 65535


class Server(models.Model):
    name = models.CharField(max_length=100)
    jar_template = models.CharField(max_length=100)
    jar = models.CharField(max_length=100, blank=True, null=True)
    port = models.IntegerField(default=25565)
    memory_limit = models.IntegerField("Memory Limit (MB)", default=1024)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    type = models.ForeignKey(
        Type, on_delete=models.CASCADE, related_name='servers')
    server_properties = models.TextField(blank=True, null=True)
    rcon_port = models.IntegerField(editable=False, default=0)
    rcon_password = models.CharField(max_length=32, editable=False, default='', blank=True)

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if self.port is None:
            return
        if self.port > MAX_GAME_PORT:
            raise ValidationError({
                'port': 'Port must leave room for the RCON port (port + 10000 must not exceed 65535).',
            })

        implied_rcon_port = self.port + 10000
        conflict = Server.objects.exclude(pk=self.pk).filter(
            Q(port=implied_rcon_port)
            | Q(rcon_port=implied_rcon_port)
            | Q(rcon_port=self.port)
        ).first()
        if conflict is not None:
            raise ValidationError({
                'port': (
                    f'This port implies RCON port {implied_rcon_port}, which conflicts with '
                    f'server "{conflict.name}" (port={conflict.port}, rcon_port={conflict.rcon_port}).'
                ),
            })


class JarDownload(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('downloading', 'Downloading'),
        ('done', 'Done'),
        ('error', 'Error'),
    ]

    provider = models.CharField(max_length=20)
    version = models.CharField(max_length=50)
    filename = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.provider} {self.version} ({self.status})'


class Backup(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('running', 'Running'),
        ('done', 'Done'),
        ('error', 'Error'),
    ]

    server = models.ForeignKey(Server, on_delete=models.CASCADE, related_name='backups')
    filename = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.server.name} backup {self.filename or "(pending)"} ({self.status})'
