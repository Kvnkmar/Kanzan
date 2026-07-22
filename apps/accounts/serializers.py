from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from apps.accounts.models import (
    Invitation,
    Permission,
    Profile,
    Role,
    TenantMembership,
    UserGroup,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# Permission
# ---------------------------------------------------------------------------


class PermissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Permission
        fields = ["id", "codename", "name", "resource", "action"]
        read_only_fields = ["id"]


# ---------------------------------------------------------------------------
# Role
# ---------------------------------------------------------------------------


class RoleSerializer(serializers.ModelSerializer):
    permissions = PermissionSerializer(many=True, read_only=True)
    permission_ids = serializers.PrimaryKeyRelatedField(
        queryset=Permission.objects.all(),
        many=True,
        write_only=True,
        source="permissions",
        required=False,
    )

    class Meta:
        model = Role
        fields = [
            "id",
            "tenant",
            "name",
            "slug",
            "description",
            "permissions",
            "permission_ids",
            "is_system",
            "hierarchy_level",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "tenant", "is_system", "created_at", "updated_at"]


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------


class UserSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "first_name",
            "last_name",
            "full_name",
            "phone",
            "avatar",
            "is_active",
            "date_joined",
        ]
        # ``is_active`` is deliberately read-only here: account activation /
        # deactivation is an admin/manager operation that goes through
        # ``UserViewSet.perform_destroy`` (which disables the *membership*), never
        # a plain PATCH of the User row. Leaving it writable let any member who
        # could reach ``UserViewSet.update`` toggle another member's login on/off.
        read_only_fields = ["id", "email", "date_joined", "is_active"]

    def get_full_name(self, obj):
        return obj.get_full_name()


class UserCreateSerializer(serializers.ModelSerializer):
    """Serializer for user registration."""

    password = serializers.CharField(
        write_only=True,
        min_length=8,
        validators=[validate_password],
    )

    class Meta:
        model = User
        fields = ["email", "password", "first_name", "last_name"]

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


class ProfileSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)

    class Meta:
        model = Profile
        fields = [
            "id",
            "user",
            "tenant",
            "job_title",
            "department",
            "bio",
            "notification_email",
            "signature",
            "timezone",
            "language",
            "dnd_enabled",
            "dnd_start",
            "dnd_end",
            "theme",
            "sidebar_collapsed",
            "density",
            "date_format",
            "time_format",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "user", "tenant", "created_at", "updated_at"]


# ---------------------------------------------------------------------------
# TenantMembership
# ---------------------------------------------------------------------------


class TenantMembershipSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    role_detail = RoleSerializer(source="role", read_only=True)
    role = serializers.PrimaryKeyRelatedField(queryset=Role.objects.none())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        tenant = getattr(request, "tenant", None) if request else None
        if tenant:
            self.fields["role"].queryset = Role.objects.filter(tenant=tenant)

    class Meta:
        model = TenantMembership
        fields = [
            "id",
            "user",
            "tenant",
            "role",
            "role_detail",
            "is_active",
            "invited_by",
            "joined_at",
        ]
        read_only_fields = ["id", "user", "tenant", "invited_by", "joined_at"]


# ---------------------------------------------------------------------------
# Invitation
# ---------------------------------------------------------------------------


class InvitationSerializer(serializers.ModelSerializer):
    invited_by = UserSerializer(read_only=True)
    role_detail = RoleSerializer(source="role", read_only=True)
    role = serializers.PrimaryKeyRelatedField(queryset=Role.objects.none())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        tenant = getattr(request, "tenant", None) if request else None
        if tenant:
            self.fields["role"].queryset = Role.objects.filter(tenant=tenant)

    class Meta:
        model = Invitation
        fields = [
            "id",
            "email",
            "role",
            "role_detail",
            "invited_by",
            "accepted_at",
            "expires_at",
            "tenant",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "invited_by",
            "accepted_at",
            "expires_at",
            "tenant",
            "created_at",
        ]

    def validate(self, attrs):
        request = self.context.get("request")
        tenant = getattr(request, "tenant", None) if request else None
        email = attrs.get("email", "").strip().lower()

        if tenant and email:
            # Check if already a tenant member
            if TenantMembership.objects.filter(
                tenant=tenant, user__email__iexact=email, is_active=True
            ).exists():
                raise serializers.ValidationError(
                    {"email": "This email is already a member of this tenant."}
                )

            # Delete expired, unaccepted invitations for this email
            Invitation.objects.filter(
                tenant=tenant,
                email__iexact=email,
                accepted_at__isnull=True,
                expires_at__lt=timezone.now(),
            ).delete()

            # Check for existing pending (non-expired, non-accepted) invitation
            if Invitation.objects.filter(
                tenant=tenant,
                email__iexact=email,
                accepted_at__isnull=True,
                expires_at__gte=timezone.now(),
            ).exists():
                raise serializers.ValidationError(
                    {
                        "email": "A pending invitation already exists for this email address."
                    }
                )

        return attrs


# ---------------------------------------------------------------------------
# UserGroup
# ---------------------------------------------------------------------------


class UserGroupSerializer(serializers.ModelSerializer):
    members = UserSerializer(many=True, read_only=True)
    member_ids = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.none(),
        many=True,
        write_only=True,
        required=False,
        source="members",
    )
    member_count = serializers.SerializerMethodField()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        tenant = getattr(request, "tenant", None) if request else None
        if tenant:
            member_ids = TenantMembership.objects.filter(
                tenant=tenant, is_active=True
            ).values_list("user_id", flat=True)
            self.fields["member_ids"].child_relation.queryset = User.objects.filter(
                id__in=member_ids
            )

    class Meta:
        model = UserGroup
        fields = [
            "id",
            "tenant",
            "name",
            "description",
            "members",
            "member_ids",
            "member_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "tenant", "created_at", "updated_at"]

    def get_member_count(self, obj):
        return obj.members.count()

    def validate_member_ids(self, value):
        """
        A user may belong to at most one UserGroup per tenant. Strict:
        any submitted user already in a *different* group blocks the save,
        even if they were a legacy member of this group before the change.
        Admins must uncheck the duplicate from one side first.
        """
        if not value:
            return value
        request = self.context.get("request")
        tenant = getattr(request, "tenant", None) if request else None
        if tenant is None:
            return value

        submitted_ids = {u.id for u in value}
        qs = UserGroup.objects.filter(
            tenant=tenant, members__id__in=submitted_ids,
        )
        if self.instance is not None:
            qs = qs.exclude(id=self.instance.id)

        conflicts = qs.values_list(
            "name",
            "members__first_name",
            "members__last_name",
            "members__email",
        ).filter(members__id__in=submitted_ids).distinct()

        if conflicts:
            parts = []
            for group_name, fn, ln, em in conflicts:
                display = (f"{fn or ''} {ln or ''}".strip()) or em
                parts.append(f"{display} is already in '{group_name}'")
            raise serializers.ValidationError(
                "A user can only belong to one group at a time. "
                "Remove them from the other group first. " + "; ".join(parts)
            )
        return value


# ---------------------------------------------------------------------------
# JWT Token (custom claim)
# ---------------------------------------------------------------------------


class TokenObtainSerializer(TokenObtainPairSerializer):
    """
    Extends SimpleJWT's TokenObtainPairSerializer to embed a ``tenant_id``
    claim when the request carries a tenant context.
    """

    tenant_id = serializers.UUIDField(required=False, write_only=True)

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["email"] = user.email
        token["full_name"] = user.get_full_name()
        return token

    def validate(self, attrs):
        data = super().validate(attrs)
        tenant_id = attrs.get("tenant_id") or getattr(
            self.context.get("request"), "tenant_id", None
        )
        tenant = getattr(self.context.get("request"), "tenant", None)

        # Prefer explicit tenant_id from payload, then request.tenant
        effective_tenant_id = tenant_id or (tenant.id if tenant else None)

        if effective_tenant_id:
            # Verify the user is actually a member of this tenant
            membership = TenantMembership.objects.filter(
                user=self.user,
                tenant_id=effective_tenant_id,
                is_active=True,
            ).first()
            if membership:
                data["tenant_id"] = str(effective_tenant_id)
                # Embed in both access and refresh tokens
                self.token["tenant_id"] = str(effective_tenant_id)
                self.token["role"] = membership.role.slug

        return data
