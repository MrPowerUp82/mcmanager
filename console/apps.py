from django.apps import AppConfig

class ConsoleConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'console'

    def ready(self) -> None:
        try:
            print('Checking server status...')
            from .views import is_server_running
            from .models import Server
            for server in Server.objects.all():
                if is_server_running(server.id):
                    server.status = True
                    server.save()
                else:
                    server.status = False
                    server.save()
            print('Server status checked!')
        except Exception as e:
            print(e)