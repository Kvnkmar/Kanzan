"""Object-level row-scoping for HubEmail viewsets.

Mirrors ``apps.accounts.permissions.IsTicketAccessible``: codename grants
from ``HasTenantPermission`` answer "can this user do this *action*?" —
this class answers "is this user allowed to see *this row*?".

Row-scoping per the Inbox Hub RBAC grid (plan section 6):

- Manager+ (effective hierarchy_level <= 20): unrestricted within tenant.
- Team Lead (25): rows whose ``department`` has lead=user OR members∋user.
- Agent / IT / HR (30): own department AND (assignee=user OR state=NEW
  for triage of unassigned mail).
- Viewer (40): own department, read-only (``HasTenantPermission``'s
  action map denies non-view actions independently).

Used only on the HubEmail viewset; Department / RoutingRule / SLA
viewsets rely on the codename check alone (those are admin-managed and
not row-scoped).
"""

from rest_framework.permissions import BasePermission

from apps.accounts.permissions import _get_membership
from apps.inbox_hub.models import HubEmail


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

        # Manager+ see everything in their tenant.
        if level <= 20:
            return True

        # Beyond this point we need the user's departments. A user with
        # no department assignment cannot see any HubEmail (Phase 1A
        # tenants without seeded Departments must add agents to one
        # explicitly).
        user_dept_ids = set(
            user.department_memberships.filter(tenant=tenant).values_list(
                "department_id", flat=True,
            )
        )
        led_dept_ids = set(
            user.led_departments.filter(tenant=tenant).values_list("id", flat=True)
        )
        visible_dept_ids = user_dept_ids | led_dept_ids

        # Team Lead: full visibility within their own departments.
        if level == 25:
            return obj.department_id in visible_dept_ids

        # Agent / IT / HR / Viewer: own department + (assigned-to-me OR
        # state=NEW for triage). HubEmails with no department (Phase 1A
        # default, before RoutingEngine lands) are visible to any
        # agent-tier user with a department membership — otherwise the
        # Hub appears empty in dev. Manager+ already returned True above.
        if obj.department_id is None:
            return bool(visible_dept_ids)

        if obj.department_id not in visible_dept_ids:
            return False

        return (
            obj.assignee_id == user.pk
            or obj.state == HubEmail.State.NEW
        )
