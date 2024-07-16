import uuid
import os
import shutil
from django.db import models
from django.conf import settings
from django.dispatch import receiver
from django.db.models.signals import post_save, pre_save, pre_delete


def get_jar_files():
    if not os.path.exists(os.path.join(settings.BASE_DIR, 'jar')):
        os.makedirs(os.path.join(settings.BASE_DIR, 'jar'))
    return [(x, x) for x in os.listdir(os.path.join(settings.BASE_DIR, 'jar')) if x.endswith('.jar')]


def get_default_server_prop():
    arq = open(os.path.join(settings.BASE_DIR,
               'configs', 'server.properties'), 'r')
    text = arq.read()
    arq.close()
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
    shutil.rmtree(os.path.join(settings.BASE_DIR,
                  'servers', f'server_{instance.id}'))


pre_delete.connect(pre_delete_server, sender=Server)


@receiver(post_save, sender=Server)
def post_save_server(sender, instance, **kwargs):
    if not instance.jar:
        instance.jar = str(instance.id) + "_" + instance.jar_template
        os.makedirs(os.path.join(settings.BASE_DIR, 'servers',
                    f'server_{instance.id}'), exist_ok=True)
        shutil.copy(os.path.join(settings.BASE_DIR, 'jar', instance.jar_template), os.path.join(
            settings.BASE_DIR, 'servers', f'server_{instance.id}', instance.jar))
        shutil.copy(os.path.join(settings.BASE_DIR, 'configs', 'server.properties'), os.path.join(
            settings.BASE_DIR, 'servers', f'server_{instance.id}', 'server.properties'))
        if instance.type.dependencies:
            for dependency in instance.type.dependencies:
                if os.path.isdir(os.path.join(settings.BASE_DIR, 'jar', dependency)):
                    shutil.copytree(os.path.join(settings.BASE_DIR, 'jar', dependency), os.path.join(
                        settings.BASE_DIR, 'servers', f'server_{instance.id}', dependency))
                else:
                    shutil.copy(os.path.join(settings.BASE_DIR, 'jar', dependency), os.path.join(
                        settings.BASE_DIR, 'servers', f'server_{instance.id}', dependency))
        with open(os.path.join(settings.BASE_DIR, 'servers', f'server_{instance.id}', 'eula.txt'), 'w') as f:
            f.write('eula=true')
        new_server_properties = []
        server_properties = open(os.path.join(
            settings.BASE_DIR, 'servers', f'server_{instance.id}', 'server.properties'), 'r')
        for line in server_properties:
            if line.startswith('server-port='):
                new_server_properties.append(f'server-port={instance.port}\n')
            else:
                new_server_properties.append(line)
        server_properties.close()
        with open(os.path.join(settings.BASE_DIR, 'servers', f'server_{instance.id}', 'server.properties'), 'w') as f:
            f.write(''.join(new_server_properties))
        server_properties = open(os.path.join(
            settings.BASE_DIR, 'servers', f'server_{instance.id}', 'server.properties'), 'r')
        text = server_properties.read()
        instance.server_properties = text
        server_properties.close()
        instance.save()
    else:
        with open(os.path.join(settings.BASE_DIR, 'servers', f'server_{instance.id}', 'server.properties'), 'w') as f:
            f.write(instance.server_properties)


post_save.connect(post_save_server, sender=Server)
