"""Access + object-level row-scoping for HubEmail viewsets.

Codename grants from ``HasTenantPermission`` answer "can this user do this
*action*?"; this module answers "may this user use the Hub at all, and may
they see *this row*?".

Access model (see :mod:`apps.inbox_hub.access` for the single source of truth):

- **Access gate** — who may open the Hub at all: Admins (level <= 10) always;
  everyone else (Manager and below) only if they belong to ≥1 ``UserGroup``. A
  non-Admin in **no** group is denied entirely (403). This is how admins
  control Hub visibility from the Groups page.
- **Row scope** — what an admitted user sees: Manager+ (level <= 20) see every
  row in the tenant; agent-tier (level > 20) see the untriaged (``state=NEW``)
  backlog plus anything assigned to them.

Used only on the HubEmail viewset; Department / RoutingRule / SLA viewsets
rely on the codename check alone (those are admin-managed and not row-scoped).
"""

from rest_framework.permissions import BasePermission

from apps.accounts.permissions import _get_membership
from apps.inbox_hub.access import user_in_any_group
from apps.inbox_hub.models import HubEmail


class HubEmailPermission(BasePermission):
    """Action-level codename gate for the HubEmail viewset.

    Kept local (rather than reusing the global ``HasTenantPermission`` +
    ``ACTION_MAP``) because action names like ``assign``/``escalate`` collide
    with the Ticket viewset's mappings. Mirrors ``HasTenantPermission``: when
    the role carries explicit permissions we check the codename; otherwise we
    fall back to the role hierarchy.

    ``claim`` and ``transition`` have no dedicated codename — they are core
    "work the email" actions, gated at agent-or-above (<= 30). Row visibility
    is enforced separately by :class:`IsHubEmailAccessible`.
    """

    ACTION_CODENAMES = {
        "list": "hub_email.view",
        "retrieve": "hub_email.view",
        "context": "hub_email.view",
        "convert_to_ticket": "hub_email.convert",
        "dismiss": "hub_email.dismiss",
        "assign": "hub_email.assign",
        "reassign": "hub_email.reassign",
        "escalate": "hub_email.escalate",
        "note": "hub_email.note",
    }
    # Actions allowed for any agent-or-above (no dedicated codename exists).
    AGENT_LEVEL_ACTIONS = {"claim", "transition"}

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

        action = view.action
        level = membership.effective_role.hierarchy_level

        # Group access gate: everyone below Admin must belong to ≥1 UserGroup
        # to touch the Hub at all (list/retrieve/actions). Admins are exempt.
        if level > 10 and not user_in_any_group(user, tenant):
            return False

        if action in self.AGENT_LEVEL_ACTIONS:
            return level <= 30

        codename = self.ACTION_CODENAMES.get(action)
        if codename is None:
            return False

        effective_perms = membership.get_effective_permissions_qs()
        if effective_perms.exists():
            return membership.has_effective_permission(codename)

        # Hierarchy fallback for tenants with no explicit permissions assigned.
        verb = codename.split(".", 1)[1]
        if verb == "view":
            return level <= 40
        if verb in ("convert", "reply", "escalate", "note"):
            return level <= 30
        return level <= 20  # assign, reassign, dismiss


class IsHubEmailAccessible(BasePermission):
    """Row-level access gate for ``HubEmail``."""

    def has_object_permission(self, request, view, obj):
        user = request.user
        tenant = getattr(request, "tenant", None)
        if tenant is None:
            return False

        membership = _get_membership(request, tenant)
        if membership is None:
            return False

        level = membership.effective_role.hierarchy_level

        # Admins are unrestricted within their tenant.
        if level <= 10:
            return True

        # Everyone below Admin must belong to a group to see any row at all
        # (access gate — ``HubEmailPermission`` already 403s the request, this
        # is defence-in-depth for direct object checks).
        if not user_in_any_group(user, tenant):
            return False

        # Manager+ (in a group) see every row; agent-tier see the untriaged
        # backlog plus anything assigned to them.
        if level <= 20:
            return True

        return (
            obj.state == HubEmail.State.NEW
            or obj.assignee_id == user.pk
        )
