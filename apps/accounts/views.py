import logging
import secrets

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from apps.accounts.models import (
    Invitation,
    Profile,
    Role,
    TenantMembership,
)
from apps.accounts.permissions import (
    HasTenantPermission,
    IsTenantAdmin,
    IsTenantAdminOrManager,
)
from apps.accounts.serializers import (
    InvitationSerializer,
    ProfileSerializer,
    RoleSerializer,
    TenantMembershipSerializer,
    TokenObtainSerializer,
    UserCreateSerializer,
    UserSerializer,
)

User = get_user_model()
logger = logging.getLogger(__name__)

INVITATION_EXPIRY_HOURS = 72


# ---------------------------------------------------------------------------
# User ViewSet
# ---------------------------------------------------------------------------


class UserViewSet(viewsets.ModelViewSet):
    """
    CRUD for users within the current tenant.
    Tenant admins can list/update/deactivate users; users can view themselves.
    """

    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated, HasTenantPermission]
    permission_resource = "user"
    search_fields = ["email", "first_name", "last_name"]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if tenant is None:
            return User.objects.none()
        member_user_ids = TenantMembership.objects.filter(
            tenant=tenant, is_active=True
        ).values_list("user_id", flat=True)
        return User.objects.filter(id__in=member_user_ids)

    def perform_destroy(self, instance):
        """Soft-deactivate: disable membership rather than deleting the user."""
        TenantMembership.objects.filter(
            user=instance, tenant=self.request.tenant
        ).update(is_active=False)

    @action(detail=False, methods=["post"], url_path="create-user",
            permission_classes=[IsAuthenticated, IsTenantAdminOrManager])
    def create_user(self, request):
        """Create a new user and add them as a tenant member with a role."""
        email = request.data.get("email", "").strip()
        password = request.data.get("password", "")
        first_name = request.data.get("first_name", "").strip()
        last_name = request.data.get("last_name", "").strip()
        role_id = request.data.get("role")

        if not email or not password:
            return Response(
                {"detail": "Email and password are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not role_id:
            return Response(
                {"detail": "Role is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        tenant = getattr(request, "tenant", None)
        if tenant is None:
            return Response(
                {"detail": "Tenant context required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            role = Role.objects.get(pk=role_id, tenant=tenant)
        except Role.DoesNotExist:
            return Response(
                {"detail": "Invalid role."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Check if user already exists
        existing_user = User.objects.filter(email=email).first()
        if existing_user:
            if TenantMembership.objects.filter(user=existing_user, tenant=tenant).exists():
                return Response(
                    {"detail": "A user with this email is already a member of this tenant."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            TenantMembership.objects.create(
                user=existing_user, tenant=tenant, role=role, invited_by=request.user,
            )
            return Response(UserSerializer(existing_user).data, status=status.HTTP_201_CREATED)

        user = User.objects.create_user(
            email=email, password=password, first_name=first_name, last_name=last_name,
        )
        TenantMembership.objects.create(
            user=user, tenant=tenant, role=role, invited_by=request.user,
        )
        return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# TenantMembership ViewSet
# ---------------------------------------------------------------------------


class TenantMembershipViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """List and update memberships for the current tenant (role changes)."""

    serializer_class = TenantMembershipSerializer
    permission_classes = [IsAuthenticated, IsTenantAdminOrManager]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if tenant is None:
            return TenantMembership.objects.none()
        return TenantMembership.objects.filter(
            tenant=tenant
        ).select_related("user", "role")


# ---------------------------------------------------------------------------
# Profile ViewSet
# ---------------------------------------------------------------------------


class ProfileViewSet(
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """
    Users manage their own tenant-scoped profile.
    """

    serializer_class = ProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if tenant is None:
            return Profile.objects.none()
        return Profile.objects.filter(tenant=tenant, user=self.request.user)

    @action(detail=False, methods=["get", "patch"])
    def me(self, request):
        """Return or update the current user's profile for this tenant."""
        tenant = getattr(request, "tenant", None)
        if tenant is None:
            return Response(
                {"detail": "Tenant context required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        profile, _created = Profile.objects.get_or_create(
            user=request.user,
            tenant=tenant,
        )
        if request.method == "PATCH":
            serializer = self.get_serializer(profile, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            return Response(serializer.data)
        serializer = self.get_serializer(profile)
        return Response(serializer.data)

    @action(
        detail=False,
        methods=["post"],
        url_path="upload-avatar",
        parser_classes=[MultiPartParser],
    )
    def upload_avatar(self, request):
        """Upload or replace the current user's avatar."""
        avatar_file = request.FILES.get("avatar")
        if not avatar_file:
            return Response(
                {"detail": "No avatar file provided."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        user = request.user
        # Delete old avatar file if it exists
        if user.avatar:
            user.avatar.delete(save=False)
        user.avatar = avatar_file
        user.save(update_fields=["avatar"])
        return Response({"avatar": user.avatar.url})


# ---------------------------------------------------------------------------
# Role ViewSet
# ---------------------------------------------------------------------------


class RoleViewSet(viewsets.ModelViewSet):
    """
    Tenant admins manage roles within their tenant.
    Admins and Managers can list/retrieve; only Admins can create/update/delete.
    """

    serializer_class = RoleSerializer

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated(), IsTenantAdminOrManager()]
        return [IsAuthenticated(), IsTenantAdmin()]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if tenant is None:
            return Role.objects.none()
        return Role.objects.filter(tenant=tenant)

    def perform_create(self, serializer):
        serializer.save(tenant=self.request.tenant)

    def perform_destroy(self, instance):
        if instance.is_system:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("System roles cannot be deleted.")
        instance.delete()


# ---------------------------------------------------------------------------
# Invitation ViewSet
# ---------------------------------------------------------------------------


class InvitationViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """
    Create and list invitations. Admins and Managers may invite.
    Managers can only invite at their hierarchy level or below.
    """

    serializer_class = InvitationSerializer
    permission_classes = [IsAuthenticated, IsTenantAdminOrManager]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if tenant is None:
            return Invitation.objects.none()
        return Invitation.objects.filter(tenant=tenant)

    def perform_create(self, serializer):
        from apps.billing.services import PlanLimitChecker

        PlanLimitChecker(self.request.tenant).check_can_add_user()

        # Validate that the inviter can only assign roles at or below their level
        role = serializer.validated_data.get("role")
        if role:
            from apps.accounts.permissions import _get_membership

            membership = _get_membership(self.request, self.request.tenant)
            if membership and role.hierarchy_level < membership.role.hierarchy_level:
                from rest_framework.exceptions import PermissionDenied

                raise PermissionDenied(
                    "You cannot invite users with a higher role than your own."
                )

        invitation = serializer.save(
            tenant=self.request.tenant,
            invited_by=self.request.user,
            token=secrets.token_urlsafe(48),
            expires_at=timezone.now() + timezone.timedelta(hours=INVITATION_EXPIRY_HOURS),
        )
        # Send invitation email
        self._send_invitation_email(invitation)

    def _send_invitation_email(self, invitation):
        """Send invitation email using Django's email backend."""
        try:
            from django.core.mail import send_mail
            from django.conf import settings as django_settings
            accept_url = f"https://{invitation.tenant.slug}.localhost:8001/accept-invitation/?token={invitation.token}"
            send_mail(
                subject=f"You've been invited to {invitation.tenant.name}",
                message=(
                    f"Hi,\n\n"
                    f"{invitation.invited_by.get_full_name()} has invited you to join "
                    f"{invitation.tenant.name} as {invitation.role.name}.\n\n"
                    f"Click here to accept: {accept_url}\n\n"
                    f"This invitation expires in {INVITATION_EXPIRY_HOURS} hours.\n\n"
                    f"- {invitation.tenant.name} Team"
                ),
                from_email=getattr(django_settings, 'DEFAULT_FROM_EMAIL', 'noreply@kanzan.local'),
                recipient_list=[invitation.email],
                fail_silently=True,
            )
            logger.info("Invitation email sent to %s", invitation.email)
        except Exception as exc:
            logger.warning("Failed to send invitation email to %s: %s", invitation.email, exc)

    def perform_destroy(self, instance):
        if instance.is_accepted:
            from rest_framework.exceptions import ValidationError
            raise ValidationError("Cannot delete an accepted invitation.")
        instance.delete()

    @action(detail=True, methods=["post"])
    def resend(self, request, pk=None):
        """Resend an invitation by generating a new token and extending expiry."""
        invitation = self.get_object()
        if invitation.is_accepted:
            return Response(
                {"detail": "Cannot resend an accepted invitation."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        invitation.token = secrets.token_urlsafe(48)
        invitation.expires_at = timezone.now() + timezone.timedelta(hours=INVITATION_EXPIRY_HOURS)
        invitation.save(update_fields=["token", "expires_at"])
        logger.info("Invitation resent to %s for tenant %s", invitation.email, invitation.tenant)
        return Response(
            InvitationSerializer(invitation).data,
            status=status.HTTP_200_OK,
        )


# ---------------------------------------------------------------------------
# Auth ViewSet
# ---------------------------------------------------------------------------


class AuthViewSet(viewsets.GenericViewSet):
    """
    Authentication endpoints: register, login (via SimpleJWT), logout,
    and accept-invitation.
    """

    permission_classes = [AllowAny]

    def get_serializer_class(self):
        if self.action == "register":
            return UserCreateSerializer
        if self.action == "login":
            return TokenObtainSerializer
        return UserSerializer

    @action(detail=False, methods=["post"])
    def register(self, request):
        """Create a new user account."""
        serializer = UserCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        refresh = RefreshToken.for_user(user)
        return Response(
            {
                "user": UserSerializer(user).data,
                "tokens": {
                    "refresh": str(refresh),
                    "access": str(refresh.access_token),
                },
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=["post"])
    def login(self, request):
        """
        Obtain JWT token pair. Delegates to SimpleJWT with custom claims.
        """
        view = TokenObtainPairView.as_view(serializer_class=TokenObtainSerializer)
        return view(request._request)

    @action(detail=False, methods=["post"], permission_classes=[IsAuthenticated])
    def logout(self, request):
        """
        Blacklist the provided refresh token.
        """
        refresh_token = request.data.get("refresh")
        if not refresh_token:
            return Response(
                {"detail": "Refresh token is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Set agent status to offline
        from apps.agents.models import AgentAvailability
        AgentAvailability.objects.filter(user=request.user).update(status="offline")

        try:
            token = RefreshToken(refresh_token)
            token.blacklist()
        except Exception:
            return Response(
                {"detail": "Invalid or already blacklisted token."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {"detail": "Successfully logged out."},
            status=status.HTTP_200_OK,
        )

    @action(
        detail=False,
        methods=["post"],
        url_path="change-password",
        permission_classes=[IsAuthenticated],
    )
    def change_password(self, request):
        """Change the authenticated user's password."""
        current_password = request.data.get("current_password", "")
        new_password = request.data.get("new_password", "")
        confirm_password = request.data.get("confirm_password", "")

        if not current_password or not new_password:
            return Response(
                {"detail": "Current password and new password are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if new_password != confirm_password:
            return Response(
                {"detail": "New passwords do not match."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if len(new_password) < 8:
            return Response(
                {"detail": "New password must be at least 8 characters."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = request.user
        if not user.check_password(current_password):
            return Response(
                {"detail": "Current password is incorrect."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(new_password)
        user.save(update_fields=["password"])

        return Response(
            {"detail": "Password changed successfully."},
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="accept-invitation")
    def accept_invitation(self, request):
        """
        Accept a tenant invitation. If the user doesn't exist, they must
        register first and then call this endpoint while authenticated.
        Unauthenticated users may accept if they provide registration data.
        """
        token = request.data.get("token")
        if not token:
            return Response(
                {"detail": "Invitation token is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            invitation = Invitation.unscoped.select_related("role", "tenant").get(
                token=token
            )
        except Invitation.DoesNotExist:
            return Response(
                {"detail": "Invalid invitation token."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if invitation.is_accepted:
            return Response(
                {"detail": "Invitation has already been accepted."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if invitation.is_expired:
            return Response(
                {"detail": "Invitation has expired."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Determine the user
        user = request.user if request.user.is_authenticated else None

        if user is None:
            # Allow inline registration
            reg_serializer = UserCreateSerializer(data=request.data)
            reg_serializer.is_valid(raise_exception=True)
            user = reg_serializer.save()

        # Verify email matches
        if user.email.lower() != invitation.email.lower():
            return Response(
                {"detail": "Invitation email does not match your account."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Create membership
        membership, created = TenantMembership.objects.get_or_create(
            user=user,
            tenant=invitation.tenant,
            defaults={
                "role": invitation.role,
                "invited_by": invitation.invited_by,
            },
        )

        if not created:
            return Response(
                {"detail": "You are already a member of this tenant."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Mark invitation as accepted
        invitation.accepted_at = timezone.now()
        invitation.save(update_fields=["accepted_at"])

        # Generate tokens
        refresh = RefreshToken.for_user(user)
        refresh["tenant_id"] = str(invitation.tenant.id)

        return Response(
            {
                "detail": "Invitation accepted successfully.",
                "user": UserSerializer(user).data,
                "tenant_id": str(invitation.tenant.id),
                "tokens": {
                    "refresh": str(refresh),
                    "access": str(refresh.access_token),
                },
            },
            status=status.HTTP_200_OK,
        )
