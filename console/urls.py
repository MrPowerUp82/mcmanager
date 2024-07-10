from django.urls import path
from . import views

urlpatterns = [
    path('<int:id>', views.index, name='index'),
    path('start_server/<int:id>', views.start_server, name='start_server'),
    path('stop_server/<int:id>', views.stop_server, name='stop_server'),
    path('force_stop_server/<int:id>', views.force_stop_server, name='force_stop_server'),
    path('view_logs/<int:id>', views.view_logs, name='view_logs'),
    path('send_command/<int:id>', views.send_command, name='send_command'),
]
