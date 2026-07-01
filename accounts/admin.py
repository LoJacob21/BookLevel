from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _

from .models import AvatarPreset, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    # O UserAdmin padrão referencia username/first_name/last_name (removidos),
    # então redefinimos fieldsets/lists para o login por email + nickname.
    ordering = ("email",)
    list_display = ("email", "nickname", "current_level", "total_xp", "is_staff")
    search_fields = ("email", "nickname")
    readonly_fields = ("last_login", "date_joined")

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (_("Perfil"), {"fields": ("nickname", "bio", "timezone", "avatar_preset")}),
        (_("Progressão"), {"fields": ("total_xp", "current_level")}),
        (_("Permissões"), {
            "fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions"),
        }),
        (_("Datas"), {"fields": ("last_login", "date_joined", "email_verified_at")}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("email", "nickname", "password1", "password2"),
        }),
    )


@admin.register(AvatarPreset)
class AvatarPresetAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "is_active")
    search_fields = ("name", "code")
    list_filter = ("is_active",)
    prepopulated_fields = {"code": ("name",)}
