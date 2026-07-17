import os

from django.conf import settings
from django.db import models


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
    status = models.BooleanField(default=False)
    server_properties = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name
