from typing import Any

from django.contrib import admin

from .models import Server, Type
from .services import provisioning


@admin.register(Server)
class ServerAdmin(admin.ModelAdmin):
    list_display = ('name', 'port', 'type', 'status')
    exclude = ('jar', 'server_properties')
    search_fields = ('name', 'port', 'type')
    list_filter = ('type', 'status')
    readonly_fields = ('status',)

    def get_form(self, request, obj=..., change=..., **kwargs) -> Any:
        if obj is None:
            self.exclude = ('jar', 'server_properties')
            self.readonly_fields = ('status',)
        else:
            self.exclude = None
            self.readonly_fields = ('status', 'jar')
        return super().get_form(request, obj, change, **kwargs)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not obj.jar:
            provisioning.create_server_files(obj)
        else:
            provisioning.sync_server_properties_file(obj)

    def delete_model(self, request, obj):
        provisioning.delete_server_files(obj)
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            provisioning.delete_server_files(obj)
        super().delete_queryset(request, queryset)


@admin.register(Type)
class TypeAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)
