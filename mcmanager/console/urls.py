from django.urls import path
from . import views, views_jars

urlpatterns = [
    path('<int:id>', views.index, name='index'),
    path('start_server/<int:id>', views.start_server, name='start_server'),
    path('stop_server/<int:id>', views.stop_server, name='stop_server'),
    path('force_stop_server/<int:id>',
         views.force_stop_server, name='force_stop_server'),
    path('view_logs/<int:id>', views.view_logs, name='view_logs'),
    path('send_command/<int:id>', views.send_command, name='send_command'),
    path('get_server_stats/<int:id>', views.get_server_stats, name='get_server_stats'),
    path('jars/', views_jars.jars_page, name='jars_page'),
    path('jars/versions/<str:provider>', views_jars.list_jar_versions, name='list_jar_versions'),
    path('jars/download', views_jars.start_jar_download, name='start_jar_download'),
    path('jars/download/<int:download_id>', views_jars.jar_download_status, name='jar_download_status'),
]
