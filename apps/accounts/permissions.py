import logging

from rest_framework.permissions import BasePermission

from apps.accounts.models import TenantMembership

logger = logging.getLogger(__name__)

# Map DRF action names to permission action verbs
ACTION_MAP = {
    "list": "view",
    "retrieve": "view",
    "create": "create",
    "update": "update",
    "partial_update": "update",
    "destroy": "delete",
    # Ticket custom actions
    "comments": "view",
    "activity": "view",
    "timeline": "view",
    "assign": "update",
    "change_status": "update",
    "change_priority": "update",
    "bulk_action": "update",
    "close": "update",
    "lookup": "view",
    "teammates": "view",
    "team_progress": "view",
    # Canned response / Saved view custom actions
    "render": "view",
    "set_default": "update",
    # Agent custom actions
    "all_members": "view",
    "online": "view",
    "workload": "view",
    "set_status": "update",
    "my_status": "view",
    # Email actions on tickets
    "emails": "view",
    "send_email": "update",
    "link_email": "update",
    "unlinked_emails": "view",
    # Knowledge base custom actions
    "record_view": "view",
    "remove_file": "update",
    "preview_file": "view",
    # Contact group custom actions
    "add_contacts": "update",
    "remove_contacts": "update",
    # Kanban custom actions
    "detail_with_cards": "view",
    "populate": "update",
    "move": "update",
    "reorder": "update",
}


def _get_membership(request, tenant):
    """
    Retrieve and cache the TenantMembership for the current user and tenant
    on the request object.
    """
    cache_attr = "_cached_tenant_membership"
    if hasattr(request, cache_attr):
        return getattr(request, cache_attr)

    membership = (
        TenantMembership.objects.select_related("role")
        .filter(user=request.user, tenant=tenant, is_active=True)
        .first()
    )
    setattr(request, cache_attr, membership)
    return membership


class HasTenantPermission(BasePermission):
    """
    Checks that the authenticated user's role within the current tenant
    includes the required permission.

    The view should set ``permission_resource`` (e.g., ``"ticket"``).
    The permission codename is derived automatically from the DRF view action:
        ``{permission_resource}.{action}``
    """

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False

        tenant = getattr(request, "tenant", None)
        if tenant is None:
            logger.warning("HasTenantPermission: no tenant on request.")
            return False

        membership = _get_membership(request, tenant)
        if membership is None:
            return False

        # Derive the required permission codename
        resource = getattr(view, "permission_resource", None)
        if resource is None:
            # If no resource is specified, allow access (view must use a
            # different permission class or handle its own logic).
            return True

        action_verb = ACTION_MAP.get(view.action)
        if action_verb is None:
            # Custom actions should define their own permission check;
            # deny by default if not mapped.
            logger.debug(
                "HasTenantPermission: unmapped action '%s' for resource '%s'.",
                view.action,
                resource,
            )
            return False

        codename = f"{resource}.{action_verb}"

        # Check explicit permissions first
        if membership.role.permissions.exists():
            return membership.role.has_permission(codename)

        # Fallback: hierarchy-based defaults when no permissions are assigned
        level = membership.role.hierarchy_level
        if action_verb == "view":
            return level <= 40          # Everyone can view
        elif action_verb in ("create", "update"):
            return level <= 30          # Agent and above
        else:
            return level <= 20          # Manager and above (delete, manage, assign, export)


class IsTicketAccessible(BasePermission):
    """
    Object-level permission that prevents Agents / Viewers from accessing
    individual tickets they did not create and are not assigned to.

    Admin and Manager roles (hierarchy_level <= 20) bypass the check.
    """

    def has_object_permission(self, request, view, obj):
        user = request.user

        tenant = getattr(request, "tenant", None)
        if tenant is None:
            return False

        membership = _get_membership(request, tenant)
        if membership is None:
            return False

        # Admin / Manager see everything in their tenant
        if membership.role.hierarchy_level <= 20:
            return True

        # Agent / Viewer: only own or assigned tickets
        return obj.created_by_id == user.pk or obj.assignee_id == user.pk


class IsTenantMember(BasePermission):
    """
    Allows access only to users who have an active TenantMembership in
    the current tenant.

    This prevents authenticated users from performing writes against
    tenants they do not belong to (e.g. via JWT from Tenant A sent to
    Tenant B's subdomain).

    Add this to any ViewSet that uses only ``IsAuthenticated`` without
    ``HasTenantPermission`` (which already checks membership).
    """

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False

        tenant = getattr(request, "tenant", None)
        if tenant is None:
            # No tenant context (main site, public endpoints) — allow through.
            return True

        membership = _get_membership(request, tenant)
        return membership is not None


class IsTenantAdmin(BasePermission):
    """
    Allows access only to users whose role hierarchy_level is <= 10
    within the current tenant (i.e., Admin-level users).
    """

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False

        tenant = getattr(request, "tenant", None)
        if tenant is None:
            return False

        membership = _get_membership(request, tenant)
        if membership is None:
            return False

        return membership.role.hierarchy_level <= 10


class IsTenantAdminOrManager(BasePermission):
    """
    Allows access to users whose role hierarchy_level is <= 20
    within the current tenant (Admin or Manager).
    """

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False

        tenant = getattr(request, "tenant", None)
        if tenant is None:
            return False

        membership = _get_membership(request, tenant)
        if membership is None:
            return False

        return membership.role.hierarchy_level <= 20
