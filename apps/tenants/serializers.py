"""
DRF serializers for the tenants app.

Provides CRUD representations for Tenant and TenantSettings resources.
"""

from rest_framework import serializers

from apps.tenants.models import Tenant, TenantSettings


class TenantSettingsSerializer(serializers.ModelSerializer):
    """
    Serializer for TenantSettings.

    Exposes all configurable fields. SSO configuration fields
    (``sso_client_id``, ``sso_authority_url``, ``sso_scopes``) are
    write-only to prevent leaking SSO metadata to non-admin users
    (since this serializer is nested in the TenantSerializer).
    """

    logo_url = serializers.SerializerMethodField()

    class Meta:
        model = TenantSettings
        fields = [
            "tenant",
            "auth_method",
            "sso_provider",
            "sso_client_id",
            "sso_client_secret",
            "sso_authority_url",
            "sso_scopes",
            "timezone",
            "date_format",
            "business_hours_start",
            "business_hours_end",
            "business_days",
            "auto_transition_on_assign",
            "auto_send_ticket_created_email",
            "auto_assign_inbound_email_tickets",
            "primary_color",
            "accent_color",
            "logo_url",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["tenant", "logo_url", "created_at", "updated_at"]
        extra_kwargs = {
            "sso_client_secret": {"write_only": True},
            "sso_client_id": {"write_only": True},
            "sso_authority_url": {"write_only": True},
            "sso_scopes": {"write_only": True},
        }

    def get_logo_url(self, obj):
        if obj.tenant.logo:
            request = self.context.get("request")
            if request:
                return request.build_absolute_uri(obj.tenant.logo.url)
            return obj.tenant.logo.url
        return None


class TenantSerializer(serializers.ModelSerializer):
    """
    Full Tenant serializer used for CRUD operations by super-admins.

    Nests a read-only representation of ``TenantSettings`` via the
    ``settings`` related name.
    """

    settings = TenantSettingsSerializer(read_only=True)

    class Meta:
        model = Tenant
        fields = [
            "id",
            "name",
            "slug",
            "domain",
            "is_active",
            "logo",
            "settings",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class TenantListSerializer(serializers.ModelSerializer):
    """
    Lightweight Tenant serializer returned for list endpoints and
    non-admin consumers.
    """

    class Meta:
        model = Tenant
        fields = [
            "id",
            "name",
            "slug",
            "domain",
            "is_active",
            "logo",
            "created_at",
        ]
        read_only_fields = fields
