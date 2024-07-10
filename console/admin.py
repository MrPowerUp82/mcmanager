from django.contrib import admin
from .models import Server, Type


admin.site.register(Server)
class TypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'jar', 'port')

admin.site.register(Type)
class TypeAdmin(admin.ModelAdmin):
    list_display = ('name',)
# Register your models here.
