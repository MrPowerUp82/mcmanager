import uuid, os, shutil
from django.db import models
from django.conf import settings
from django.dispatch import receiver
from django.db.models.signals import post_save, pre_save, pre_delete

def get_jar_files(self):
    if not os.path.exists(os.path.join(settings.BASE_DIR, 'jar')):
        os.makedirs(os.path.join(settings.BASE_DIR, 'jar'))
    return ((x, x) for x in os.listdir(os.path.join(settings.BASE_DIR, 'jar')) if x.endswith('.jar'))

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
    ip = models.GenericIPAddressField()
    port = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    type = models.ForeignKey(Type, on_delete=models.CASCADE, related_name='servers')

    def __str__(self):
        return self.name

@receiver(pre_delete, sender=Server)
def pre_delete_server(sender, instance, **kwargs):
    shutil.rmtree(os.path.join(settings.BASE_DIR, 'servers', f'server_{instance.id}'))

@receiver(post_save, sender=Server)
def post_save_server(sender, instance, **kwargs):
    if instance.jar:
        instance.jar = str(instance.id) + "_" + instance.jar_template
        os.makedirs(os.path.join(settings.BASE_DIR, 'servers', f'server_{instance.id}'), exist_ok=True)
        shutil.copy(os.path.join(settings.BASE_DIR, 'jar', instance.jar_template), os.path.join(settings.BASE_DIR, 'servers', f'server_{instance.id}', instance.jar))
        if instance.type.dependencies:
            for dependency in instance.type.dependencies:
                shutil.copy(os.path.join(settings.BASE_DIR, 'jar', dependency), os.path.join(settings.BASE_DIR, 'servers', f'server_{instance.id}', dependency))