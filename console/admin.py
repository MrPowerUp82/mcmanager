from django.contrib import admin
from .models import Server, Type


@admin.register(Server)
class ServerAdmin(admin.ModelAdmin):
    list_display = ('name', 'port', 'type', 'status')
    exclude = ('jar',)
    search_fields = ('name', 'port', 'type')
    list_filter = ('type','status')

@admin.register(Type)
class TypeAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)
