import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models

from main.models import TenantScopedModel

from apps.accounts.managers import UserManager


class User(AbstractUser):
    """
    Custom user model using email as the unique identifier.
    Users are global (not tenant-scoped) and can belong to multiple tenants
    through TenantMembership.
    """

    username = None

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField("email address", unique=True)
    first_name = models.CharField("first name", max_length=150)
    last_name = models.CharField("last name", max_length=150)
    phone = models.CharField("phone number", max_length=20, null=True, blank=True)
    avatar = models.ImageField(upload_to="avatars/", null=True, blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["first_name", "last_name"]

    objects = UserManager()

    class Meta:
        verbose_name = "user"
        verbose_name_plural = "users"
        ordering = ["-date_joined"]

    def __str__(self):
        return self.email

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    def get_short_name(self):
        return self.first_name


class Permission(models.Model):
    """
    Application-level permission. Global (not tenant-scoped).
    Codenames follow the pattern: resource.action (e.g., 'ticket.create').
    """

    class Action(models.TextChoices):
        VIEW = "view", "View"
        CREATE = "create", "Create"
        UPDATE = "update", "Update"
        DELETE = "delete", "Delete"
        ASSIGN = "assign", "Assign"
        EXPORT = "export", "Export"
        MANAGE = "manage", "Manage"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    codename = models.CharField(max_length=100, unique=True, help_text="e.g. 'ticket.create'")
    name = models.CharField(max_length=255, help_text="Human-readable permission name")
    resource = models.CharField(max_length=50, help_text="e.g. 'ticket', 'contact', 'billing'")
    action = models.CharField(max_length=20, choices=Action.choices)

    class Meta:
        verbose_name = "permission"
        verbose_name_plural = "permissions"
        ordering = ["resource", "action"]

    def __str__(self):
        return self.codename


class Role(TenantScopedModel):
    """
    Tenant-scoped role. Each tenant can define its own roles with custom
    permission sets. System roles are pre-created and cannot be deleted.
    """

    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100)
    description = models.TextField(blank=True, default="")
    permissions = models.ManyToManyField(
        Permission,
        blank=True,
        related_name="roles",
    )
    is_system = models.BooleanField(
        default=False,
        help_text="System roles are pre-created and cannot be deleted by tenants.",
    )
    hierarchy_level = models.PositiveIntegerField(
        default=100,
        help_text="Lower value = higher authority. Admin=10, Manager=20, etc.",
    )

    class Meta:
        verbose_name = "role"
        verbose_name_plural = "roles"
        ordering = ["hierarchy_level", "name"]
        unique_together = [("tenant", "slug")]

    def __str__(self):
        return f"{self.name} ({self.tenant})"

    def has_permission(self, codename):
        """Check whether this role includes the given permission codename."""
        return self.permissions.filter(codename=codename).exists()


class Profile(TenantScopedModel):
    """
    Tenant-scoped user profile. One profile per user per tenant, holding
    tenant-specific information like job title and notification preferences.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profiles",
    )
    job_title = models.CharField(max_length=100, null=True, blank=True)
    department = models.CharField(max_length=100, null=True, blank=True)
    bio = models.TextField(null=True, blank=True)
    notification_email = models.BooleanField(
        default=True,
        help_text="Receive email notifications for this tenant.",
    )

    class Meta:
        verbose_name = "profile"
        verbose_name_plural = "profiles"
        ordering = ["-created_at"]
        unique_together = [("user", "tenant")]

    def __str__(self):
        return f"Profile of {self.user.email} in {self.tenant}"


class TenantMembership(models.Model):
    """
    Links a User to a Tenant with a specific Role. Controls access
    and permissions within each tenant.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE,
        related_name="members",
    )
    role = models.ForeignKey(
        "accounts.Role",
        on_delete=models.PROTECT,
        related_name="members",
    )
    is_active = models.BooleanField(default=True)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invitations_sent",
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "tenant membership"
        verbose_name_plural = "tenant memberships"
        ordering = ["-joined_at"]
        unique_together = [("user", "tenant")]

    def __str__(self):
        return f"{self.user.email} -> {self.tenant} ({self.role.name})"


class Invitation(TenantScopedModel):
    """
    Tenant-scoped invitation. Allows existing members to invite new users
    to join a tenant with a specific role.
    """

    email = models.EmailField()
    role = models.ForeignKey(
        "accounts.Role",
        on_delete=models.CASCADE,
        related_name="invitations",
    )
    token = models.CharField(max_length=64, unique=True)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="created_invitations",
    )
    accepted_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()

    class Meta:
        verbose_name = "invitation"
        verbose_name_plural = "invitations"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Invitation for {self.email} to {self.tenant}"

    @property
    def is_expired(self):
        from django.utils import timezone

        return timezone.now() > self.expires_at

    @property
    def is_accepted(self):
        return self.accepted_at is not None
