from typing import Any

from django.contrib import admin

from .forms import ServerForm
from .models import Server, Type
from .services import process, provisioning


@admin.register(Server)
class ServerAdmin(admin.ModelAdmin):
    form = ServerForm
    list_display = ('name', 'port', 'type', 'is_running')
    exclude = ('jar', 'server_properties', 'desired_running', 'consecutive_restart_failures', 'last_scheduled_backup_date')
    search_fields = ('name', 'port', 'type')
    list_filter = ('type',)
    readonly_fields = ()

    def is_running(self, obj):
        return process.is_running(obj)
    is_running.boolean = True
    is_running.short_description = 'Running'

    def get_form(self, request, obj=..., change=..., **kwargs) -> Any:
        internal_fields = ('desired_running', 'consecutive_restart_failures', 'last_scheduled_backup_date')
        if obj is None:
            self.exclude = ('jar', 'server_properties') + internal_fields
            self.readonly_fields = ()
        else:
            self.exclude = internal_fields
            self.readonly_fields = ('jar',)
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
