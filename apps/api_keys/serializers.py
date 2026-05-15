"""
Serializers for the API-keys management surface.

Three serializers cover the three response shapes:

* ``APIKeyListSerializer`` / ``APIKeyDetailSerializer`` — safe read-only views.
  Cleartext is **never** included.
* ``APIKeyCreateSerializer`` — input-only payload for ``POST /api/v1/api-keys/``.
  Routes through ``apps.api_keys.services.create_api_key`` in the viewset; we
  intentionally do **not** subclass ``ModelSerializer`` so the viewset stays in
  control of side effects (service-user creation, audit log, email task).
* ``APIKeyCreatedResponseSerializer`` — the "show once" response that the
  viewset hand-builds with the cleartext stitched on top of the detail payload.
"""

from __future__ import annotations

from rest_framework import serializers

from apps.accounts.models import Role
from apps.api_keys.models import APIKey


class APIKeyListSerializer(serializers.ModelSerializer):
    """
    Read-only summary used by both ``list`` and ``retrieve`` (see
    ``APIKeyDetailSerializer`` below for the alias). Exposes the safe-to-display
    masked prefix; never returns the SHA-512 hash.
    """

    role_name = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()
    masked_prefix = serializers.ReadOnlyField()

    class Meta:
        model = APIKey
        fields = [
            "id",
            "name",
            "prefix",
            "masked_prefix",
            "role",
            "role_name",
            "is_active",
            "created_at",
            "last_used_at",
            "last_used_ip",
            "expires_at",
            "request_count",
            "created_by",
            "created_by_name",
        ]
        read_only_fields = fields

    def get_role_name(self, obj):
        role = getattr(obj, "role", None)
        return role.name if role else None

    def get_created_by_name(self, obj):
        user = getattr(obj, "created_by", None)
        if not user:
            return None
        full = user.get_full_name() if hasattr(user, "get_full_name") else ""
        return full or getattr(user, "email", "")


class APIKeyDetailSerializer(APIKeyListSerializer):
    """
    Same shape as the list serializer — the list payload is already detailed
    enough for the retrieve endpoint. Kept as a distinct class so the viewset
    can route ``retrieve`` separately and future divergence is cheap.
    """


class APIKeyCreateSerializer(serializers.Serializer):
    """
    Input-only serializer for ``POST /api/v1/api-keys/``.

    Validation responsibilities:

    * ``name`` must be unique per-tenant.
    * ``role_id`` (if supplied) must belong to the current tenant.

    The actual mint flow runs in the viewset's ``create`` override, which calls
    ``services.create_api_key`` and constructs the one-time response payload.
    """

    name = serializers.CharField(max_length=120, required=True)
    role_id = serializers.UUIDField(required=False, allow_null=True)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)

    def _get_tenant(self):
        request = self.context.get("request")
        if request is None:
            return None
        # Prefer the cached membership-resolved tenant set by HasTenantPermission
        # (it is identical to request.tenant once both are populated, but the
        # cached value is faster and matches the spec the plan calls out).
        cached_membership = getattr(request, "_cached_tenant_membership", None)
        if cached_membership is not None:
            return getattr(cached_membership, "tenant", None) or getattr(request, "tenant", None)
        return getattr(request, "tenant", None)

    def validate_name(self, value):
        tenant = self._get_tenant()
        if tenant is None:
            raise serializers.ValidationError("Tenant context is required.")
        qs = APIKey.unscoped.filter(tenant=tenant, name=value)
        if qs.exists():
            raise serializers.ValidationError(
                "An API key with this name already exists in this tenant."
            )
        return value

    def validate_role_id(self, value):
        if value is None:
            return value
        tenant = self._get_tenant()
        if tenant is None:
            raise serializers.ValidationError("Tenant context is required.")
        if not Role.unscoped.filter(tenant=tenant, pk=value).exists():
            raise serializers.ValidationError(
                "Role does not belong to the current tenant."
            )
        return value

    def create(self, validated_data):
        # Service-layer side effects happen in the viewset's `create()`
        # override; this method exists only so DRF's framework contract is
        # satisfied (e.g. browsable-API rendering).
        return validated_data

    def update(self, instance, validated_data):
        return validated_data


class APIKeyCreatedResponseSerializer(serializers.Serializer):
    """
    One-time response shape returned by ``create`` and ``regenerate``.

    Mirrors every field of ``APIKeyDetailSerializer`` plus the ``key``
    cleartext. The cleartext is populated by the viewset on response
    construction and **never** persisted anywhere.
    """

    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    prefix = serializers.CharField(read_only=True)
    masked_prefix = serializers.CharField(read_only=True)
    role = serializers.UUIDField(read_only=True)
    role_name = serializers.CharField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    last_used_at = serializers.DateTimeField(read_only=True, allow_null=True)
    last_used_ip = serializers.IPAddressField(read_only=True, allow_null=True)
    expires_at = serializers.DateTimeField(read_only=True, allow_null=True)
    request_count = serializers.IntegerField(read_only=True)
    created_by = serializers.UUIDField(read_only=True)
    created_by_name = serializers.CharField(read_only=True)
    key = serializers.CharField(read_only=True, help_text="Cleartext API key (shown once).")
