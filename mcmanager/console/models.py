import uuid
import os
import shutil
from django.db import models
from django.conf import settings
from django.dispatch import receiver
from django.db.models.signals import post_save, pre_save, pre_delete


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
    jar_template = models.CharField(max_length=100, choices=get_jar_files())
    jar = models.CharField(max_length=100, blank=True, null=True)
    # ip = models.GenericIPAddressField(blank=True, null=True)
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


@receiver(pre_delete, sender=Server)
def pre_delete_server(sender, instance, **kwargs):
    servers_dir = getattr(settings, 'SERVERS_DIR', os.path.join(settings.BASE_DIR, 'servers'))
    server_path = os.path.join(servers_dir, f'server_{instance.id}')
    if os.path.exists(server_path):
        shutil.rmtree(server_path)


pre_delete.connect(pre_delete_server, sender=Server)


@receiver(post_save, sender=Server)
def post_save_server(sender, instance, **kwargs):
    servers_dir = getattr(settings, 'SERVERS_DIR', os.path.join(settings.BASE_DIR, 'servers'))
    jar_dir = getattr(settings, 'JAR_DIR', os.path.join(settings.BASE_DIR, 'jar'))
    configs_dir = getattr(settings, 'CONFIGS_DIR', os.path.join(settings.BASE_DIR, 'configs'))

    server_path = os.path.join(servers_dir, f'server_{instance.id}')
    properties_path = os.path.join(server_path, 'server.properties')

    if not instance.jar:
        instance.jar = str(instance.id) + "_" + instance.jar_template
        os.makedirs(server_path, exist_ok=True)
        shutil.copy(os.path.join(jar_dir, instance.jar_template), os.path.join(server_path, instance.jar))
        shutil.copy(os.path.join(configs_dir, 'server.properties'), properties_path)
        
        if instance.type.dependencies:
            for dependency in instance.type.dependencies:
                dep_source = os.path.join(jar_dir, dependency)
                dep_dest = os.path.join(server_path, dependency)
                if os.path.isdir(dep_source):
                    shutil.copytree(dep_source, dep_dest)
                else:
                    shutil.copy(dep_source, dep_dest)
                    
        with open(os.path.join(server_path, 'eula.txt'), 'w', encoding='utf-8') as f:
            f.write('eula=true')
            
        new_server_properties = []
        with open(properties_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                if line.startswith('server-port='):
                    new_server_properties.append(f'server-port={instance.port}\n')
                else:
                    new_server_properties.append(line)
                    
        with open(properties_path, 'w', encoding='utf-8') as f:
            f.write(''.join(new_server_properties))
            
        with open(properties_path, 'r', encoding='utf-8', errors='ignore') as f:
            text = f.read()
            
        instance.server_properties = text
        instance.save()
    else:
        with open(properties_path, 'w', encoding='utf-8') as f:
            f.write(instance.server_properties)


post_save.connect(post_save_server, sender=Server)

