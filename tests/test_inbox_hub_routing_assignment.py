"""Inbox Hub Phase 1B tests — presence, routing, and presence-gated assignment.

Covers the core "email → department → online agent" pipeline:
1. Presence: is_assignable freshness/capacity gating; reap_stale_presence.
2. RoutingEngine: rule match, order/stop_on_match, default-department fallback.
3. AssignmentEngine: picks online dept agent; holds when none online;
   load-balances; respects department scope.
4. drain_department_backlog: assigns held NEW emails when an agent comes online.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from conftest import InboundEmailFactory, QueueFactory, TenantFactory, UserFactory
from main.context import clear_current_tenant, set_current_tenant


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _availability(tenant, user, *, status="online", last_seen="fresh", capacity=10, load=0):
    from apps.agents.models import AgentAvailability, AgentStatus

    if last_seen == "fresh":
        seen = timezone.now()
    elif last_seen == "stale":
        seen = timezone.now() - timedelta(seconds=600)
    else:
        seen = last_seen  # None or explicit datetime
    return AgentAvailability.unscoped.create(
        tenant=tenant,
        user=user,
        status=getattr(AgentStatus, status.upper()),
        last_seen=seen,
        max_concurrent_tickets=capacity,
        current_ticket_count=load,
    )


def _department(tenant, lead, *, slug="general", name="General", default_queue=None):
    from apps.inbox_hub.models import Department

    return Department.unscoped.create(
        tenant=tenant, name=name, slug=slug, lead=lead, default_queue=default_queue,
    )


def _enroll(tenant, department, user):
    from apps.inbox_hub.models import DepartmentMembership

    return DepartmentMembership.unscoped.create(tenant=tenant, department=department, user=user)


def _online_dept_agent(tenant, department, *, capacity=10, load=0, status="online", last_seen="fresh"):
    user = UserFactory()
    _enroll(tenant, department, user)
    _availability(tenant, user, status=status, last_seen=last_seen, capacity=capacity, load=load)
    return user


def _hub_email(tenant, *, department=None, queue=None, priority=None, sender="a@acme.com",
               recipient="support+demo@kanzan.io", subject="Hello"):
    from apps.inbox_hub.models import HubEmail

    inbound = InboundEmailFactory(
        tenant=tenant, sender_email=sender, recipient_email=recipient, subject=subject,
    )
    return HubEmail.unscoped.create(
        tenant=tenant,
        inbound=inbound,
        state=HubEmail.State.NEW,
        priority=priority or HubEmail.Priority.NORMAL,
        department=department,
        queue=queue,
    )


# ---------------------------------------------------------------------------
# Presence
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPresenceIsAssignable:
    def test_online_fresh_with_capacity_is_assignable(self):
        tenant = TenantFactory()
        a = _availability(tenant, UserFactory(), status="online", last_seen="fresh")
        assert a.is_assignable is True

    def test_online_but_stale_heartbeat_not_assignable(self):
        tenant = TenantFactory()
        a = _availability(tenant, UserFactory(), status="online", last_seen="stale")
        assert a.is_assignable is False

    def test_online_no_heartbeat_not_assignable(self):
        tenant = TenantFactory()
        a = _availability(tenant, UserFactory(), status="online", last_seen=None)
        assert a.is_assignable is False

    @pytest.mark.parametrize("status", ["away", "busy", "offline"])
    def test_non_online_status_not_assignable(self, status):
        tenant = TenantFactory()
        a = _availability(tenant, UserFactory(), status=status, last_seen="fresh")
        assert a.is_assignable is False

    def test_at_capacity_not_assignable(self):
        tenant = TenantFactory()
        a = _availability(tenant, UserFactory(), status="online", last_seen="fresh",
                          capacity=2, load=2)
        assert a.is_assignable is False


@pytest.mark.django_db
class TestReapStalePresence:
    def test_reaper_flips_stale_online_to_away(self):
        from apps.agents.models import AgentAvailability, AgentStatus
        from apps.agents.tasks import reap_stale_presence

        tenant = TenantFactory()
        stale = _availability(tenant, UserFactory(), status="online", last_seen="stale")
        fresh = _availability(tenant, UserFactory(), status="online", last_seen="fresh")

        reaped = reap_stale_presence()

        stale.refresh_from_db()
        fresh.refresh_from_db()
        assert reaped == 1
        assert stale.status == AgentStatus.AWAY
        assert fresh.status == AgentStatus.ONLINE


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRoutingEngine:
    def test_rule_matches_sender_domain_sets_department_and_queue(self):
        from apps.inbox_hub.models import RoutingRule
        from apps.inbox_hub.routing import RoutingEngine

        tenant = TenantFactory()
        lead = UserFactory()
        queue = QueueFactory(tenant=tenant, name="Billing")
        billing = _department(tenant, lead, slug="billing", name="Billing")
        RoutingRule.unscoped.create(
            tenant=tenant, name="Acme→Billing", order=10,
            match={"sender_domain": ["acme.com"]},
            department=billing, queue=queue, stop_on_match=True,
        )
        he = _hub_email(tenant, sender="ap@acme.com")

        decision = RoutingEngine.classify_and_route(he)
        he.refresh_from_db()

        assert decision.department == billing
        assert he.department_id == billing.id
        assert he.queue_id == queue.id

    def test_no_match_falls_back_to_default_department(self):
        from apps.inbox_hub.routing import RoutingEngine

        tenant = TenantFactory()
        lead = UserFactory()
        general = _department(tenant, lead, slug="general", name="General")
        tenant.settings.inbox_hub_default_department = general
        tenant.settings.save(update_fields=["inbox_hub_default_department", "updated_at"])

        he = _hub_email(tenant, sender="someone@unknown.io")
        decision = RoutingEngine.classify_and_route(he)
        he.refresh_from_db()

        assert decision.matched_rule is None
        assert he.department_id == general.id

    def test_order_and_stop_on_match(self):
        from apps.inbox_hub.models import RoutingRule
        from apps.inbox_hub.routing import RoutingEngine

        tenant = TenantFactory()
        lead = UserFactory()
        d1 = _department(tenant, lead, slug="first", name="First")
        d2 = _department(tenant, lead, slug="second", name="Second")
        # Both rules match, but lower order wins and stops.
        RoutingRule.unscoped.create(
            tenant=tenant, name="first", order=10,
            match={"keyword": ["invoice"]}, department=d1, stop_on_match=True,
        )
        RoutingRule.unscoped.create(
            tenant=tenant, name="second", order=20,
            match={"keyword": ["invoice"]}, department=d2, stop_on_match=True,
        )
        he = _hub_email(tenant, subject="Please send the invoice")

        decision = RoutingEngine.classify_and_route(he)
        assert decision.department == d1


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAssignmentEngine:
    def test_assigns_to_online_department_agent(self):
        from apps.inbox_hub.assignment import AssignmentEngine
        from apps.inbox_hub.models import HubEmail

        tenant = TenantFactory()
        lead = UserFactory()
        dept = _department(tenant, lead)
        agent = _online_dept_agent(tenant, dept)
        he = _hub_email(tenant, department=dept)

        result = AssignmentEngine.try_assign(he)
        he.refresh_from_db()

        assert result == agent
        assert he.assignee_id == agent.id
        assert he.state == HubEmail.State.ASSIGNED
        assert he.first_assigned_at is not None

    def test_holds_when_no_agent_online(self):
        from apps.inbox_hub.assignment import AssignmentEngine
        from apps.inbox_hub.models import HubEmail

        tenant = TenantFactory()
        lead = UserFactory()
        dept = _department(tenant, lead)
        # Agent present but stale (effectively offline).
        _online_dept_agent(tenant, dept, last_seen="stale")
        he = _hub_email(tenant, department=dept)

        result = AssignmentEngine.try_assign(he)
        he.refresh_from_db()

        assert result is None
        assert he.assignee_id is None
        assert he.state == HubEmail.State.NEW  # held

    def test_load_balances_to_least_loaded(self):
        from apps.inbox_hub.assignment import AssignmentEngine

        tenant = TenantFactory()
        lead = UserFactory()
        dept = _department(tenant, lead)
        busy = _online_dept_agent(tenant, dept, load=5)
        idle = _online_dept_agent(tenant, dept, load=0)
        he = _hub_email(tenant, department=dept)

        result = AssignmentEngine.try_assign(he)
        assert result == idle
        assert result != busy

    def test_does_not_assign_outside_department(self):
        from apps.inbox_hub.assignment import AssignmentEngine
        from apps.inbox_hub.models import HubEmail

        tenant = TenantFactory()
        lead = UserFactory()
        dept_a = _department(tenant, lead, slug="a", name="A")
        dept_b = _department(tenant, lead, slug="b", name="B")
        # Online agent only in dept B.
        _online_dept_agent(tenant, dept_b)
        he = _hub_email(tenant, department=dept_a)

        result = AssignmentEngine.try_assign(he)
        he.refresh_from_db()
        assert result is None
        assert he.state == HubEmail.State.NEW


@pytest.mark.django_db
class TestDrainDepartmentBacklog:
    def test_drains_held_emails_when_agent_comes_online(self):
        from apps.inbox_hub.assignment import drain_department_backlog
        from apps.inbox_hub.models import HubEmail

        tenant = TenantFactory()
        lead = UserFactory()
        dept = _department(tenant, lead)
        # Two held (NEW, unassigned) emails in the department.
        he1 = _hub_email(tenant, department=dept, subject="first")
        he2 = _hub_email(tenant, department=dept, subject="second")
        # Agent comes online.
        agent = _online_dept_agent(tenant, dept)

        assigned = drain_department_backlog(agent, tenant)
        he1.refresh_from_db()
        he2.refresh_from_db()

        assert assigned == 2
        assert he1.assignee_id == agent.id
        assert he2.assignee_id == agent.id
        assert he1.state == HubEmail.State.ASSIGNED

    def test_drain_respects_remaining_capacity(self):
        from apps.inbox_hub.assignment import drain_department_backlog
        from apps.inbox_hub.models import HubEmail

        tenant = TenantFactory()
        lead = UserFactory()
        dept = _department(tenant, lead)
        for i in range(3):
            _hub_email(tenant, department=dept, subject=f"held-{i}")
        # Capacity 1, already 0 load → can take exactly 1.
        agent = _online_dept_agent(tenant, dept, capacity=1, load=0)

        assigned = drain_department_backlog(agent, tenant)
        assert assigned == 1
        assert HubEmail.unscoped.filter(
            tenant=tenant, assignee=agent, state=HubEmail.State.ASSIGNED,
        ).count() == 1


# ---------------------------------------------------------------------------
# End-to-end: real park entry point fires the full route → assign chain
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestParkPipelineEndToEnd:
    """Drives park_email_in_hub (the real entry) so the on-commit
    route → SLA → assign chain runs, mirroring what happens when an inbound
    email arrives with the Hub enabled. The single most representative test."""

    def _setup(self, tenant):
        """Online agent in a default department; hub enabled."""
        from apps.accounts.models import Role, TenantMembership

        admin = UserFactory()
        admin_role = Role.unscoped.get(tenant=tenant, slug="admin")
        TenantMembership.objects.create(user=admin, tenant=tenant, role=admin_role, is_active=True)

        dept = _department(tenant, admin, slug="general", name="General")
        agent = _online_dept_agent(tenant, dept)

        s = tenant.settings
        s.inbox_hub_enabled = True
        s.inbox_hub_auto_assign = True
        s.inbox_hub_default_department = dept
        s.save()
        return admin, dept, agent

    def test_park_routes_and_auto_assigns_to_online_agent(self, django_capture_on_commit_callbacks):
        from apps.inbox_hub.models import HubEmail
        from apps.inbox_hub.services import park_email_in_hub

        tenant = TenantFactory()
        set_current_tenant(tenant)
        try:
            admin, dept, agent = self._setup(tenant)
            inbound = InboundEmailFactory(
                tenant=tenant, sender_email="cust@acme.com",
                recipient_email=f"support+{tenant.slug}@kanzan.io", subject="Help",
            )
            # Capture so the on_commit(_post_park_hooks) chain actually runs.
            with django_capture_on_commit_callbacks(execute=True):
                park_email_in_hub(inbound, tenant, None, admin)

            he = HubEmail.unscoped.get(inbound=inbound)
            assert he.department_id == dept.id          # routed
            assert he.assignee_id == agent.id           # auto-assigned to online agent
            assert he.state == HubEmail.State.ASSIGNED
        finally:
            clear_current_tenant()

    def test_park_holds_when_no_agent_online(self, django_capture_on_commit_callbacks):
        from apps.inbox_hub.models import HubEmail
        from apps.inbox_hub.services import park_email_in_hub

        tenant = TenantFactory()
        set_current_tenant(tenant)
        try:
            admin, dept, agent = self._setup(tenant)
            # Take the only agent offline (stale heartbeat).
            from apps.agents.models import AgentAvailability
            from django.utils import timezone
            from datetime import timedelta
            AgentAvailability.unscoped.filter(tenant=tenant, user=agent).update(
                last_seen=timezone.now() - timedelta(seconds=600),
            )
            inbound = InboundEmailFactory(
                tenant=tenant, sender_email="cust@acme.com",
                recipient_email=f"support+{tenant.slug}@kanzan.io", subject="Held",
            )
            with django_capture_on_commit_callbacks(execute=True):
                park_email_in_hub(inbound, tenant, None, admin)

            he = HubEmail.unscoped.get(inbound=inbound)
            assert he.department_id == dept.id          # still routed
            assert he.assignee_id is None               # but held
            assert he.state == HubEmail.State.NEW
        finally:
            clear_current_tenant()


# ---------------------------------------------------------------------------
# Action API
# ---------------------------------------------------------------------------


def _api_hub_email(tenant):
    from apps.inbox_hub.models import HubEmail

    inbound = InboundEmailFactory(
        tenant=tenant, recipient_email=f"support+{tenant.slug}@kanzan.io", subject="Act on me",
    )
    return HubEmail.unscoped.create(
        tenant=tenant, inbound=inbound,
        state=HubEmail.State.NEW, priority=HubEmail.Priority.NORMAL,
    )


@pytest.mark.django_db
class TestHubEmailActionsApi:
    def test_admin_assign_to_agent(self, admin_client, tenant, agent_user):
        he = _api_hub_email(tenant)
        resp = admin_client.post(
            f"/api/v1/inbox-hub/hub-emails/{he.id}/assign/",
            {"assignee_id": str(agent_user.id)}, format="json",
        )
        assert resp.status_code == 200
        he.refresh_from_db()
        assert he.assignee_id == agent_user.id
        assert he.state == "assigned"

    def test_assign_rejects_non_member(self, admin_client, tenant):
        he = _api_hub_email(tenant)
        outsider = UserFactory()
        resp = admin_client.post(
            f"/api/v1/inbox-hub/hub-emails/{he.id}/assign/",
            {"assignee_id": str(outsider.id)}, format="json",
        )
        assert resp.status_code == 400

    def test_admin_escalate(self, admin_client, tenant):
        he = _api_hub_email(tenant)
        resp = admin_client.post(
            f"/api/v1/inbox-hub/hub-emails/{he.id}/escalate/",
            {"reason": "urgent"}, format="json",
        )
        assert resp.status_code == 200
        he.refresh_from_db()
        assert he.escalation_count == 1

    def test_transition_valid(self, admin_client, tenant):
        he = _api_hub_email(tenant)
        resp = admin_client.post(
            f"/api/v1/inbox-hub/hub-emails/{he.id}/transition/",
            {"state": "assigned"}, format="json",
        )
        assert resp.status_code == 200
        he.refresh_from_db()
        assert he.state == "assigned"

    def test_transition_invalid_returns_400(self, admin_client, tenant):
        he = _api_hub_email(tenant)
        # NEW -> IN_PROGRESS is illegal (NEW only goes to ASSIGNED/ESCALATED/...).
        resp = admin_client.post(
            f"/api/v1/inbox-hub/hub-emails/{he.id}/transition/",
            {"state": "in_progress"}, format="json",
        )
        assert resp.status_code == 400

    def test_add_note(self, admin_client, tenant):
        he = _api_hub_email(tenant)
        resp = admin_client.post(
            f"/api/v1/inbox-hub/hub-emails/{he.id}/note/",
            {"body": "internal triage note"}, format="json",
        )
        assert resp.status_code == 201
        assert resp.data["body"] == "internal triage note"

    def test_department_list_visible_to_member(self, agent_client, tenant, agent_user):
        lead = UserFactory()
        _department(tenant, lead, slug="general", name="General")
        resp = agent_client.get("/api/v1/inbox-hub/departments/")
        assert resp.status_code == 200

    def test_routing_rule_write_denied_to_agent(self, agent_client, tenant):
        resp = agent_client.post(
            "/api/v1/inbox-hub/routing-rules/",
            {"name": "x", "match": {"keyword": ["hi"]}}, format="json",
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Department access gate
#
# The Inbox Hub is department-scoped: supervisors (Manager+) always; agent-tier
# only if they belong to a Department; Viewers never. Once a tenant HAS
# departments, an agent in none of them is locked out (403, zeroed badge). A
# tenant with NO departments falls open for agents (out-of-box behaviour). See
# apps/inbox_hub/access.py.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestInboxHubDepartmentGate:
    def test_agent_without_department_denied_when_departments_exist(
        self, agent_client, tenant, admin_user
    ):
        _department(tenant, admin_user, slug="sales")  # tenant now has a dept
        _api_hub_email(tenant)
        resp = agent_client.get("/api/v1/inbox-hub/hub-emails/")
        assert resp.status_code == 403

    def test_agent_falls_open_when_no_departments(self, agent_client, tenant):
        """No departments configured → the gate stays open for agent-tier."""
        _api_hub_email(tenant)
        resp = agent_client.get("/api/v1/inbox-hub/hub-emails/")
        assert resp.status_code == 200
        assert resp.data["count"] >= 1

    def test_agent_in_department_can_list(self, agent_client, tenant, agent_user, admin_user):
        dept = _department(tenant, admin_user, slug="support")
        _enroll(tenant, dept, agent_user)
        # NEW mail routed to the agent's department is visible.
        he = _api_hub_email(tenant)
        he.department = dept
        he.save(update_fields=["department", "updated_at"])
        resp = agent_client.get("/api/v1/inbox-hub/hub-emails/")
        assert resp.status_code == 200
        assert resp.data["count"] >= 1

    def test_admin_without_department_can_list(self, admin_client, tenant, admin_user):
        """Admins always bypass the gate."""
        _department(tenant, admin_user, slug="sales")
        _api_hub_email(tenant)
        resp = admin_client.get("/api/v1/inbox-hub/hub-emails/")
        assert resp.status_code == 200

    def test_manager_without_department_can_list(self, manager_client, tenant, admin_user):
        """Managers are the supervisory see-all tier — no department needed."""
        _department(tenant, admin_user, slug="sales")
        _api_hub_email(tenant)
        resp = manager_client.get("/api/v1/inbox-hub/hub-emails/")
        assert resp.status_code == 200

    def test_agent_without_department_denied_retrieve(
        self, agent_client, tenant, admin_user
    ):
        _department(tenant, admin_user, slug="sales")
        he = _api_hub_email(tenant)
        resp = agent_client.get(f"/api/v1/inbox-hub/hub-emails/{he.id}/")
        assert resp.status_code == 403

    def test_agent_in_department_can_retrieve_new(
        self, agent_client, tenant, agent_user, admin_user
    ):
        dept = _department(tenant, admin_user, slug="support")
        _enroll(tenant, dept, agent_user)
        he = _api_hub_email(tenant)
        he.department = dept
        he.save(update_fields=["department", "updated_at"])
        resp = agent_client.get(f"/api/v1/inbox-hub/hub-emails/{he.id}/")
        assert resp.status_code == 200

    def test_badge_zero_for_agent_without_department(
        self, agent_client, tenant, admin_user
    ):
        _department(tenant, admin_user, slug="sales")
        _api_hub_email(tenant)
        resp = agent_client.get("/api/v1/nav/badge-counts/")
        assert resp.status_code == 200
        assert resp.data["inbox_hub"] == 0

    def test_badge_counts_for_agent_in_department(
        self, agent_client, tenant, agent_user, admin_user
    ):
        dept = _department(tenant, admin_user, slug="support")
        _enroll(tenant, dept, agent_user)
        _api_hub_email(tenant)
        resp = agent_client.get("/api/v1/nav/badge-counts/")
        assert resp.status_code == 200
        assert resp.data["inbox_hub"] >= 1

    def test_badge_scoped_to_own_department(
        self, agent_client, tenant, agent_user, admin_user
    ):
        """The badge counts only the agent's own department's NEW mail — a NEW
        email routed to another department must not inflate it."""
        mine = _department(tenant, admin_user, slug="support")
        theirs = _department(tenant, admin_user, slug="sales")
        _enroll(tenant, mine, agent_user)

        he_mine = _api_hub_email(tenant)
        he_mine.department = mine
        he_mine.save(update_fields=["department", "updated_at"])
        he_theirs = _api_hub_email(tenant)
        he_theirs.department = theirs
        he_theirs.save(update_fields=["department", "updated_at"])

        resp = agent_client.get("/api/v1/nav/badge-counts/")
        assert resp.status_code == 200
        assert resp.data["inbox_hub"] == 1  # only my department's NEW counted

    def test_badge_counts_for_manager_without_department(
        self, manager_client, tenant, admin_user
    ):
        _department(tenant, admin_user, slug="sales")
        _api_hub_email(tenant)
        resp = manager_client.get("/api/v1/nav/badge-counts/")
        assert resp.status_code == 200
        assert resp.data["inbox_hub"] >= 1

    def test_badge_counts_for_admin_without_department(self, admin_client, tenant):
        """Admins are exempt — badge still counts."""
        _api_hub_email(tenant)
        resp = admin_client.get("/api/v1/nav/badge-counts/")
        assert resp.status_code == 200
        assert resp.data["inbox_hub"] >= 1
