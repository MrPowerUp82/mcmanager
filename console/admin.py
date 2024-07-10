from typing import Any
from django.contrib import admin
from django.http import HttpRequest
from django.http.response import HttpResponse
from .models import Server, Type


@admin.register(Server)
class ServerAdmin(admin.ModelAdmin):
    list_display = ('name', 'port', 'type', 'status')
    exclude = ('jar','server_properties')
    search_fields = ('name', 'port', 'type')
    list_filter = ('type','status')
    readonly_fields = ('status',)

    def get_form(self, request: Any, obj: Any | None = ..., change: bool = ..., **kwargs: Any) -> Any:
        if obj is None:
            self.exclude = ('jar','server_properties')
            self.readonly_fields = ('status',)
        else:
            self.exclude = None
            self.readonly_fields = ('status','jar')
        return super().get_form(request, obj, change, **kwargs)

@admin.register(Type)
class TypeAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)
