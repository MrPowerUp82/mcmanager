from django.contrib import admin
from django.urls import path, include
from django.conf.urls.static import static
from django.conf import settings
from console.views import home

urlpatterns = [
    path('', home, name='home'),
    path('admin/', admin.site.urls),
    path('console/', include('console.urls')),
] + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)