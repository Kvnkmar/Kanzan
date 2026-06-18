"""Inbox Hub access gating + row visibility — department-scoped (team inbox).

Two questions, one source of truth.

**Access gate** — may this member open the Hub at all?

* Admin / Manager (``hierarchy_level <= 20``) always — the supervisory
  "see everything" tier (mirrors Front / Zendesk / Help Scout, where
  admins and supervisors bypass per-inbox scoping).
* Viewer (``hierarchy_level > 30``) never — read-only roles don't triage
  customer email (this is the role floor that stops a Viewer reading the
  whole backlog just by being grouped).
* Agent-tier (Team Lead / Agent / IT / HR, levels ``21..30``) only if they
  belong to ≥1 active :class:`~apps.inbox_hub.models.Department`. Two safety
  valves stop this from locking people out: if the tenant has **no**
  departments configured yet the Hub stays open to all agents (out-of-box /
  transition behaviour), and an agent always keeps access to mail **assigned
  to them** even if they are in none of its departments (covers the
  no-department auto-assign fallback).

**Row visibility** — which rows does an admitted user see?

* Admin / Manager (``<= 20``): every row in the tenant.
* Agent-tier: the untriaged (``state=NEW``) backlog **for their own
  departments**, plus unrouted mail that has no department (the shared
  triage pool), plus anything assigned to them. A Sales agent no longer sees
  Support's backlog.

This supersedes the previous binary ``UserGroup`` gate, under which being in
*any* group unlocked the *whole* backlog and the specific group was
irrelevant. ``Department`` already carries members, a default queue and
routing, so it is the natural access boundary and keeps auto-assignment and
visibility on the same axis (see ``assignment.drain_department_backlog``,
which already scopes the held-mail pool the same way).

Single source of truth consulted by:

* ``apps.inbox_hub.permissions`` (API gate + row visibility),
* ``apps.inbox_hub.views.HubEmailViewSet.get_queryset`` (list scoping),
* ``apps.tenants.frontend_views.inbox_hub_page`` (page gate),
* ``apps.tenants.context_processors`` (sidebar visibility flag),
* ``apps.nav.views.BadgeCountView`` (badge count).
"""

from django.db.models import Q

#: Admin + Manager: supervisory tier — bypass department scoping (see all).
SUPERVISOR_HIERARCHY_LEVEL = 20
#: Agent-or-above floor: roles *above* this (Viewer = 40) cannot triage.
AGENT_HIERARCHY_LEVEL = 30


def user_department_ids(user, tenant):
    """Active department ids *user* belongs to within *tenant* (a ``set``).

    Only **active** departments count — membership of a soft-disabled
    (``is_active=False``) department neither grants Hub access nor surfaces its
    mail, matching ``tenant_has_departments``. This is the single helper behind
    the gate, the list queryset, the object check and the badge, so the
    active-filter applies uniformly.
    """
    if user is None or not getattr(user, "is_authenticated", False) or tenant is None:
        return set()
    from apps.inbox_hub.models import DepartmentMembership

    return set(
        DepartmentMembership.unscoped.filter(
            tenant=tenant, user=user, department__is_active=True
        ).values_list("department_id", flat=True)
    )


def tenant_has_departments(tenant):
    """Whether *tenant* has ≥1 active Department (the gate falls open if not)."""
    if tenant is None:
        return False
    from apps.inbox_hub.models import Department

    return Department.unscoped.filter(tenant=tenant, is_active=True).exists()


def _has_assigned_hub_email(user, tenant):
    """Whether *user* has any HubEmail assigned to them in *tenant*.

    The black-hole safety valve: the no-department auto-assign fallback can
    hand an agent mail in a department they're not a member of. They must
    still be able to open the Hub to work it.
    """
    if user is None or tenant is None:
        return False
    from apps.inbox_hub.models import HubEmail

    return HubEmail.unscoped.filter(tenant=tenant, assignee=user).exists()


def can_access_inbox_hub(membership, *, user=None, tenant=None):
    """Whether the member behind *membership* may open the Inbox Hub.

    See the module docstring for the full rule. *user* and *tenant* are
    optional fast-paths so callers that already hold them avoid
    re-dereferencing ``membership`` (which would hit the DB if unfetched).
    """
    if membership is None:
        return False
    level = membership.effective_role.hierarchy_level
    if level <= SUPERVISOR_HIERARCHY_LEVEL:
        return True  # Admin + Manager: always
    if level > AGENT_HIERARCHY_LEVEL:
        return False  # Viewer: never (role floor)

    # Agent-tier: scoped to their department(s), with two safety valves.
    u = user or membership.user
    t = tenant or membership.tenant
    if user_department_ids(u, t):
        return True
    if not tenant_has_departments(t):
        return True  # no departments configured yet → fall open
    return _has_assigned_hub_email(u, t)  # keep access to mail assigned to me


def hub_rows_q(user, department_ids):
    """``Q`` for agent-tier list visibility — the queryset twin of
    :func:`agent_can_see_hub_email`; the two MUST stay in lock-step."""
    from apps.inbox_hub.models import HubEmail

    in_my_dept = Q(department_id__in=list(department_ids)) | Q(department__isnull=True)
    return (Q(state=HubEmail.State.NEW) & in_my_dept) | Q(assignee_id=user.pk)


def agent_can_see_hub_email(obj, user, department_ids):
    """Object-level twin of :func:`hub_rows_q`.

    Agent-tier sees a row iff it is assigned to them, or it is untriaged
    (``state=NEW``) and either belongs to one of their departments or has no
    department at all (the shared pool).
    """
    from apps.inbox_hub.models import HubEmail

    if obj.assignee_id == user.pk:
        return True
    if obj.state != HubEmail.State.NEW:
        return False
    return obj.department_id is None or obj.department_id in department_ids
