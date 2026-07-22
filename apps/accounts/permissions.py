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
    "change_stage": "update",
    "change_priority": "update",
    "bulk_action": "update",
    "close": "update",
    "escalate": "update",
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
    "send_creation_email": "update",
    "link_email": "update",
    "unlinked_emails": "view",
    "mark_all_read": "view",
    # Knowledge base custom actions
    "record_view": "view",
    "remove_file": "update",
    "preview_file": "view",
    "submit_for_review": "update",
    "approve": "update",
    "reject": "update",
    "vote": "view",
    # Contact custom actions
    "context": "view",
    # Inbound-email custom actions
    "attachment": "view",
    # Contact group custom actions
    "add_contacts": "update",
    "remove_contacts": "update",
    # Kanban custom actions
    "detail_with_cards": "view",
    "populate": "update",
    "move": "update",
    "reorder": "update",
    # Ticket linking / merge / split / macros
    "links": "update",
    "delete_link": "update",
    "merge": "update",
    "split": "update",
    "apply_macro": "update",
    # Ticket search
    "search": "view",
    # Ticket watchers / time tracking / templates / webhooks
    "watchers": "view",
    "remove_watcher": "update",
    "watch": "view",
    "time_entries": "view",
    "time_summary": "view",
    "time_entry_detail": "update",
    "restore": "update",
    "use": "view",
    "test": "update",
    "reset_failures": "update",
    # Reminder custom actions
    "overdue": "view",
    "complete": "update",
    "cancel": "update",
    "reschedule": "update",
    "stats": "view",
    # News feed custom actions
    "react": "view",
    "mark_read": "view",
    "mark_all_read": "view",
    "unread_count": "view",
    # VoIP custom actions
    "active_calls": "view",
    "call_stats": "view",
    # Inbox Hub custom actions. NB: ``assign``/``escalate`` are deliberately
    # NOT remapped here — those action names collide with TicketViewSet (which
    # maps them to ``update``). The Inbox Hub viewset uses its own
    # ``HubEmailPermission`` class so its action→codename map stays local.
    "convert_to_ticket": "convert",
    "dismiss": "dismiss",
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
        TenantMembership.objects.select_related("role", "temporary_role")
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

        effective_role = membership.effective_role

        # A curated `temporary_permissions` allow-list is a HARD restriction:
        # an admin deliberately narrowed an active temporary role to a subset,
        # so enforce it strictly with NO hierarchy fall-through. (Outside this
        # case, get_effective_permissions_qs() == the effective role's perms.)
        if (
            membership.has_active_temporary_role
            and membership.temporary_permissions.exists()
        ):
            return membership.has_effective_permission(codename)

        # Otherwise, explicit role permissions are ADDITIVE over the hierarchy
        # floor: an explicit grant allows, and anything NOT explicitly granted
        # falls through to the hierarchy default below.
        #
        # This deliberately replaces the old all-or-nothing branch (which, the
        # moment a role held ANY explicit perm, hard-DENIED every codename the
        # role lacked — including resources with no codename in the catalogue at
        # all). With the catalogue now fully seeded and roles carrying their
        # blueprint perms, that all-or-nothing form would 403 legitimate actions
        # (e.g. Admin on custom-fields/kanban, which have no codename). Treating
        # grants as additive makes fine-grained perms meaningful — Team Lead's
        # delete/export, an Agent's ticket.assign — while the hierarchy floor
        # guarantees a catalogue gap never silently locks anyone out.
        if membership.has_effective_permission(codename):
            return True

        # Hierarchy floor: sensible defaults keyed on role seniority.
        level = effective_role.hierarchy_level
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
        if membership.effective_role.hierarchy_level <= 20:
            return True

        # Agent / Viewer: tickets assigned to them, or created by them and not
        # yet handed off to another agent (see apps.tickets.access for the rule
        # shared with the list queryset, badge count and kanban scoping).
        from apps.tickets.access import agent_can_see_ticket

        return agent_can_see_ticket(user, obj)


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

        return membership.effective_role.hierarchy_level <= 10


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

        return membership.effective_role.hierarchy_level <= 20


class IsSelfOrTenantManager(BasePermission):
    """
    Object-level permission: allow Admin/Manager (effective hierarchy <= 20)
    to act on any member, and allow any member to act on *their own* record.

    Used for ``UserViewSet`` writes so an Agent can edit their own profile but
    cannot modify (or, with ``is_active`` read-only, disable) another member —
    closing the hierarchy-floor gap where ``user.update`` fell through to the
    ``<=30`` create/update default.
    """

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        # Must be an active member of the current tenant to reach the object
        # check at all (blocks a JWT from tenant A used against tenant B).
        tenant = getattr(request, "tenant", None)
        if tenant is None:
            return False
        return _get_membership(request, tenant) is not None

    def has_object_permission(self, request, view, obj):
        tenant = getattr(request, "tenant", None)
        if tenant is None:
            return False
        membership = _get_membership(request, tenant)
        if membership is None:
            return False
        if membership.effective_role.hierarchy_level <= 20:
            return True
        return obj == request.user


class IsAgentOrAbove(BasePermission):
    """Active member whose effective role is Agent-tier or higher (hierarchy
    ``<= 30``) — i.e. everyone except Viewer (level 40).

    Use this to gate WRITE actions on viewsets that would otherwise rely on the
    RBAC hierarchy floor, WITHOUT routing custom ``@action`` method names through
    ``ACTION_MAP`` (an unmapped action falls through ``HasTenantPermission`` and
    is denied, silently breaking the feature). Fails closed when the user is not
    an active member of the Host-resolved tenant, so it also blocks a foreign
    JWT and an offboarded member.
    """

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        tenant = getattr(request, "tenant", None)
        if tenant is None:
            return False
        membership = _get_membership(request, tenant)
        return (
            membership is not None
            and membership.effective_role.hierarchy_level <= 30
        )
