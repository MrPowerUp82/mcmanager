from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('start_server/', views.start_server, name='start_server'),
    path('stop_server/', views.stop_server, name='stop_server'),
    path('force_stop_server/', views.force_stop_server, name='force_stop_server'),
    path('view_logs/', views.view_logs, name='view_logs'),
    path('send_command/', views.send_command, name='send_command'),
]
