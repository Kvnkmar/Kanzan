"""Inbox Hub access gating — group-membership based.

A tenant member may use the Inbox Hub only if **either**:

* their effective role is Admin (``hierarchy_level <= 10``), **or**
* they belong to at least one :class:`~apps.accounts.models.UserGroup` in the
  tenant.

Everyone below Admin — Manager, Team Lead, Agent, IT, HR, Viewer — who is in
**no** group is fully locked out of the Hub: no sidebar entry, a zeroed badge,
and a 403 on both the page and the API. This lets administrators control who
can triage email purely by adding/removing people on the Groups page — a
brand-new member who hasn't been placed in a group sees nothing.

Only Admins are intentionally exempt: they manage the groups and, given the
"one user per group per tenant" rule, can't always be a member themselves.

This module is the single source of truth consulted by:
* ``apps.inbox_hub.permissions`` (API gate + row visibility),
* ``apps.inbox_hub.views.HubEmailViewSet.get_queryset`` (list scoping),
* ``apps.tenants.frontend_views.inbox_hub_page`` (page gate),
* ``apps.tenants.context_processors`` (sidebar visibility flag),
* ``apps.nav.views.BadgeCountView`` (badge count).
"""

#: Members at or above this hierarchy level (Admin) bypass the group gate.
ADMIN_HIERARCHY_LEVEL = 10


def user_in_any_group(user, tenant):
    """Return ``True`` if *user* belongs to ≥1 ``UserGroup`` within *tenant*."""
    if user is None or not getattr(user, "is_authenticated", False) or tenant is None:
        return False
    from apps.accounts.models import UserGroup

    return UserGroup.unscoped.filter(tenant=tenant, members=user).exists()


def can_access_inbox_hub(membership, *, user=None, tenant=None):
    """Whether the member behind *membership* may use the Inbox Hub.

    Admins always may; everyone else (Manager and below) must belong to ≥1
    group. *user* and *tenant* are optional fast-paths so callers that already
    hold them avoid re-dereferencing ``membership`` (which would hit the DB if
    unfetched).
    """
    if membership is None:
        return False
    if membership.effective_role.hierarchy_level <= ADMIN_HIERARCHY_LEVEL:
        return True
    return user_in_any_group(user or membership.user, tenant or membership.tenant)
