from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from apps.accounts.models import (
    Invitation,
    Permission,
    Profile,
    Role,
    TenantMembership,
    User,
)


# ---------------------------------------------------------------------------
# User Admin
# ---------------------------------------------------------------------------


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Custom admin for the email-based User model."""

    list_display = ("email", "first_name", "last_name", "is_staff", "is_active", "date_joined")
    list_filter = ("is_staff", "is_superuser", "is_active")
    search_fields = ("email", "first_name", "last_name")
    ordering = ("-date_joined",)

    # Override fieldsets to remove username references
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("first_name", "last_name", "phone", "avatar")}),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ),
            },
        ),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "email",
                    "first_name",
                    "last_name",
                    "password1",
                    "password2",
                ),
            },
        ),
    )


# ---------------------------------------------------------------------------
# Profile Admin
# ---------------------------------------------------------------------------


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "tenant", "job_title", "department", "notification_email")
    list_filter = ("tenant", "notification_email")
    search_fields = ("user__email", "user__first_name", "user__last_name", "job_title")
    raw_id_fields = ("user", "tenant")


# ---------------------------------------------------------------------------
# TenantMembership Admin
# ---------------------------------------------------------------------------


@admin.register(TenantMembership)
class TenantMembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "tenant", "role", "is_active", "joined_at")
    list_filter = ("tenant", "role", "is_active")
    search_fields = ("user__email", "tenant__name")
    raw_id_fields = ("user", "tenant", "role", "invited_by")


# ---------------------------------------------------------------------------
# Role Admin
# ---------------------------------------------------------------------------


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "tenant", "hierarchy_level", "is_system")
    list_filter = ("tenant", "is_system")
    search_fields = ("name", "slug")
    filter_horizontal = ("permissions",)
    raw_id_fields = ("tenant",)


# ---------------------------------------------------------------------------
# Permission Admin
# ---------------------------------------------------------------------------


@admin.register(Permission)
class PermissionAdmin(admin.ModelAdmin):
    list_display = ("codename", "name", "resource", "action")
    list_filter = ("resource", "action")
    search_fields = ("codename", "name")


# ---------------------------------------------------------------------------
# Invitation Admin
# ---------------------------------------------------------------------------


@admin.register(Invitation)
class InvitationAdmin(admin.ModelAdmin):
    list_display = ("email", "tenant", "role", "invited_by", "accepted_at", "expires_at")
    list_filter = ("tenant",)
    search_fields = ("email",)
    raw_id_fields = ("tenant", "role", "invited_by")
