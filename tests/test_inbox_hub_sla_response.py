"""Regression tests for the Inbox-Hub SLA-response fix (2026-07-02).

Pins the three-part fix that stops actively-triaged emails from
false-breaching their response SLA and auto-escalating on every sweep:

* TestFirstRespondedStamping   -- RESPONDER triage actions (claim /
  self-assign / convert) stamp ``first_responded_at``; a manager merely
  ROUTING mail to someone else, engine auto-assignment, and dismiss
  deliberately do NOT.
* TestTriagedEmailNoBreach     -- a self-claimed overdue email no longer
  breaches; manager-routed and auto-assigned untouched mail still does
  (the sat-on-mail durability layer stays intact for routed mail).
* TestEscalationCountGating    -- ``escalate_hub_email`` only acts on a
  genuine transition into ESCALATED; re-escalating an already-ESCALATED
  row no longer bumps ``escalation_count`` or re-nudges the lead; the
  API surfaces the no-op as a 400.
* TestTerminalStateGuard       -- the convert<->dismiss cross-transitions
  are rejected by the state machine under a row lock (service
  ``ValueError``, API 400 — including the second caller on the personal
  Inbox page); the idempotent re-convert / re-dismiss paths and the
  CONVERTED-with-deleted-ticket recovery path keep working.
* TestSlaRiskLens              -- the ?sla_risk=true lens mirrors the
  sweep: active + un-breached + un-responded rows only.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from main.context import tenant_context

from conftest import ContactFactory, InboundEmailFactory, TicketFactory, TicketStatusFactory


def _hub(tenant, *, state, assignee=None, contact=None,
         sla_response_due_at=None, first_responded_at=None, **extra):
    from apps.inbox_hub.models import HubEmail

    inbound = InboundEmailFactory(
        tenant=tenant,
        recipient_email=f"support+{tenant.slug}@crm.io",
        sender_email="customer@example.com",
    )
    return HubEmail.unscoped.create(
        tenant=tenant,
        inbound=inbound,
        state=state,
        priority=HubEmail.Priority.NORMAL,
        assignee=assignee,
        contact=contact,
        sla_response_due_at=sla_response_due_at,
        first_responded_at=first_responded_at,
        **extra,
    )


@pytest.mark.django_db
class TestFirstRespondedStamping:
    def test_self_claim_stamps_first_responded_at(self, tenant, agent_user):
        from apps.inbox_hub.models import HubEmail
        from apps.inbox_hub.services import reassign_hub_email

        hub = _hub(tenant, state=HubEmail.State.NEW)
        with tenant_context(tenant):
            reassign_hub_email(hub, agent_user, actor=agent_user, reason="claimed")
        hub.refresh_from_db()
        assert hub.state == HubEmail.State.ASSIGNED
        assert hub.first_responded_at is not None

    def test_manager_routing_does_not_stamp(self, tenant, admin_user, agent_user):
        """The ROUTER is not the responder — manager-assigned mail keeps its
        SLA deadline armed so the sat-on-mail breach still protects it."""
        from apps.inbox_hub.models import HubEmail
        from apps.inbox_hub.services import reassign_hub_email

        hub = _hub(tenant, state=HubEmail.State.NEW)
        with tenant_context(tenant):
            reassign_hub_email(hub, agent_user, actor=admin_user, reason="routing")
        hub.refresh_from_db()
        assert hub.state == HubEmail.State.ASSIGNED
        assert hub.first_responded_at is None

    def test_claim_via_api_stamps_first_responded_at(self, agent_client, tenant):
        from apps.inbox_hub.models import HubEmail

        hub = _hub(tenant, state=HubEmail.State.NEW)
        resp = agent_client.post(f"/api/v1/inbox-hub/hub-emails/{hub.id}/claim/")
        assert resp.status_code == 200
        hub.refresh_from_db()
        assert hub.first_responded_at is not None

    def test_existing_stamp_is_not_overwritten(self, tenant, admin_user, agent_user):
        from apps.inbox_hub.models import HubEmail
        from apps.inbox_hub.services import reassign_hub_email

        original = timezone.now() - timedelta(hours=1)
        hub = _hub(
            tenant, state=HubEmail.State.ASSIGNED,
            assignee=agent_user, first_responded_at=original,
        )
        with tenant_context(tenant):
            reassign_hub_email(hub, admin_user, actor=admin_user, reason="handoff")
        hub.refresh_from_db()
        assert hub.first_responded_at == original

    def test_engine_auto_assign_does_not_stamp(self, tenant, agent_user):
        from apps.inbox_hub.assignment import assign_to
        from apps.inbox_hub.models import HubEmail, HubEmailAssignment

        hub = _hub(tenant, state=HubEmail.State.NEW)
        with tenant_context(tenant):
            assigned = assign_to(
                hub, agent_user, reason=HubEmailAssignment.Reason.AUTO,
            )
        assert assigned == agent_user
        hub.refresh_from_db()
        assert hub.state == HubEmail.State.ASSIGNED
        assert hub.first_responded_at is None

    def test_convert_stamps_first_responded_at(self, tenant, admin_user):
        from apps.inbox_hub.models import HubEmail
        from apps.inbox_hub.services import convert_to_ticket

        with tenant_context(tenant):
            TicketStatusFactory(tenant=tenant, is_default=True, name="Open", slug="open")
            contact = ContactFactory(tenant=tenant, email="customer@example.com")
            hub = _hub(tenant, state=HubEmail.State.NEW, contact=contact)
            convert_to_ticket(hub, actor=admin_user)
        hub.refresh_from_db()
        assert hub.state == HubEmail.State.CONVERTED_TO_TICKET
        assert hub.first_responded_at is not None

    def test_dismiss_does_not_stamp(self, tenant, admin_user):
        """Dismissal is a discard, not a response — leave the metric unset."""
        from apps.inbox_hub.models import HubEmail
        from apps.inbox_hub.services import dismiss_hub_email

        hub = _hub(tenant, state=HubEmail.State.NEW)
        with tenant_context(tenant):
            dismiss_hub_email(hub, actor=admin_user, reason="spam")
        hub.refresh_from_db()
        assert hub.state == HubEmail.State.DISMISSED
        assert hub.first_responded_at is None


@pytest.mark.django_db
class TestTriagedEmailNoBreach:
    def test_self_claimed_overdue_email_does_not_breach(self, tenant, agent_user):
        from apps.inbox_hub.models import HubEmail
        from apps.inbox_hub.services import reassign_hub_email
        from apps.inbox_hub.tasks import check_hub_sla_breaches

        hub = _hub(
            tenant, state=HubEmail.State.NEW,
            sla_response_due_at=timezone.now() - timedelta(minutes=5),
        )
        with tenant_context(tenant):
            reassign_hub_email(hub, agent_user, actor=agent_user, reason="claimed")
        check_hub_sla_breaches()
        hub.refresh_from_db()
        assert hub.response_breached is False
        assert hub.escalation_count == 0
        assert hub.state == HubEmail.State.ASSIGNED

    def test_manager_routed_untouched_email_still_breaches(
        self, tenant, admin_user, agent_user
    ):
        """Routing is not responding: mail a manager parked on an agent who
        never touched it must still breach + escalate (durability layer)."""
        from apps.inbox_hub.models import HubEmail
        from apps.inbox_hub.services import reassign_hub_email
        from apps.inbox_hub.tasks import check_hub_sla_breaches

        hub = _hub(
            tenant, state=HubEmail.State.NEW,
            sla_response_due_at=timezone.now() - timedelta(minutes=5),
        )
        with tenant_context(tenant):
            reassign_hub_email(hub, agent_user, actor=admin_user, reason="routing")
        check_hub_sla_breaches()
        hub.refresh_from_db()
        assert hub.response_breached is True
        assert hub.escalation_count == 1
        assert hub.state == HubEmail.State.ESCALATED

    def test_auto_assigned_untouched_email_still_breaches(self, tenant, agent_user):
        """The durability layer survives: auto-assigned mail nobody touched
        must still breach + escalate when the deadline passes."""
        from apps.inbox_hub.assignment import assign_to
        from apps.inbox_hub.models import HubEmail, HubEmailAssignment
        from apps.inbox_hub.tasks import check_hub_sla_breaches

        hub = _hub(
            tenant, state=HubEmail.State.NEW,
            sla_response_due_at=timezone.now() - timedelta(minutes=5),
        )
        with tenant_context(tenant):
            assign_to(hub, agent_user, reason=HubEmailAssignment.Reason.AUTO)
        check_hub_sla_breaches()
        hub.refresh_from_db()
        assert hub.response_breached is True
        assert hub.escalation_count == 1
        assert hub.state == HubEmail.State.ESCALATED


@pytest.mark.django_db
class TestEscalationCountGating:
    def test_re_escalate_escalated_row_is_noop(self, tenant, agent_user):
        from apps.inbox_hub.models import HubEmail
        from apps.inbox_hub.services import escalate_hub_email

        hub = _hub(tenant, state=HubEmail.State.ESCALATED, escalation_count=1)
        with tenant_context(tenant):
            escalate_hub_email(hub, actor=agent_user, reason="again")
        hub.refresh_from_db()
        assert hub.escalation_count == 1
        assert hub.state == HubEmail.State.ESCALATED

    def test_sweep_does_not_bump_already_escalated(self, tenant, agent_user):
        """A manually-escalated email whose deadline then passes gets the
        breach flag but no second escalation event."""
        from apps.inbox_hub.models import HubEmail
        from apps.inbox_hub.tasks import check_hub_sla_breaches

        hub = _hub(
            tenant, state=HubEmail.State.ESCALATED,
            assignee=agent_user, escalation_count=1,
            sla_response_due_at=timezone.now() - timedelta(minutes=5),
        )
        check_hub_sla_breaches()
        hub.refresh_from_db()
        assert hub.response_breached is True
        assert hub.escalation_count == 1

    def test_escalate_from_resolved_is_noop(self, tenant, agent_user):
        from apps.inbox_hub.models import HubEmail
        from apps.inbox_hub.services import escalate_hub_email

        hub = _hub(tenant, state=HubEmail.State.RESOLVED)
        with tenant_context(tenant):
            escalate_hub_email(hub, actor=agent_user, reason="late")
        hub.refresh_from_db()
        assert hub.escalation_count == 0
        assert hub.state == HubEmail.State.RESOLVED

    def test_escalate_api_surfaces_noop_as_400(self, admin_client, tenant):
        """The service no-ops silently (the sweep needs that); the API must
        not pretend an illegal escalation succeeded."""
        from apps.inbox_hub.models import HubEmail

        escalated = _hub(tenant, state=HubEmail.State.ESCALATED, escalation_count=1)
        resp = admin_client.post(
            f"/api/v1/inbox-hub/hub-emails/{escalated.id}/escalate/", {},
        )
        assert resp.status_code == 400
        escalated.refresh_from_db()
        assert escalated.escalation_count == 1

        dismissed = _hub(tenant, state=HubEmail.State.DISMISSED)
        resp = admin_client.post(
            f"/api/v1/inbox-hub/hub-emails/{dismissed.id}/escalate/", {},
        )
        assert resp.status_code == 400


@pytest.mark.django_db
class TestTerminalStateGuard:
    def test_convert_dismissed_raises(self, tenant, admin_user):
        from apps.inbox_hub.models import HubEmail
        from apps.inbox_hub.services import convert_to_ticket

        hub = _hub(tenant, state=HubEmail.State.DISMISSED)
        with tenant_context(tenant), pytest.raises(ValueError):
            convert_to_ticket(hub, actor=admin_user)

    def test_dismiss_converted_raises(self, tenant, admin_user):
        from apps.inbox_hub.models import HubEmail
        from apps.inbox_hub.services import dismiss_hub_email

        hub = _hub(tenant, state=HubEmail.State.CONVERTED_TO_TICKET)
        with tenant_context(tenant), pytest.raises(ValueError):
            dismiss_hub_email(hub, actor=admin_user, reason="oops")

    def test_convert_dismissed_api_returns_400(self, admin_client, tenant):
        from apps.inbox_hub.models import HubEmail

        hub = _hub(tenant, state=HubEmail.State.DISMISSED)
        resp = admin_client.post(
            f"/api/v1/inbox-hub/hub-emails/{hub.id}/convert-to-ticket/", {},
        )
        assert resp.status_code == 400
        hub.refresh_from_db()
        assert hub.state == HubEmail.State.DISMISSED

    def test_dismiss_converted_api_returns_400(self, admin_client, tenant):
        from apps.inbox_hub.models import HubEmail

        hub = _hub(tenant, state=HubEmail.State.CONVERTED_TO_TICKET)
        resp = admin_client.post(
            f"/api/v1/inbox-hub/hub-emails/{hub.id}/dismiss/", {},
        )
        assert resp.status_code == 400
        hub.refresh_from_db()
        assert hub.state == HubEmail.State.CONVERTED_TO_TICKET

    def test_re_dismiss_stays_idempotent(self, admin_client, tenant):
        from apps.inbox_hub.models import HubEmail

        hub = _hub(tenant, state=HubEmail.State.DISMISSED)
        resp = admin_client.post(
            f"/api/v1/inbox-hub/hub-emails/{hub.id}/dismiss/", {},
        )
        assert resp.status_code == 200

    def test_re_convert_returns_existing_ticket(self, tenant, admin_user):
        """The idempotent guard still short-circuits ahead of the state
        machine: a CONVERTED row with a live ticket returns that ticket."""
        from apps.inbox_hub.models import HubEmail
        from apps.inbox_hub.services import convert_to_ticket

        with tenant_context(tenant):
            ticket = TicketFactory(tenant=tenant)
            hub = _hub(
                tenant, state=HubEmail.State.CONVERTED_TO_TICKET,
                converted_ticket=ticket,
            )
            result = convert_to_ticket(hub, actor=admin_user)
        assert result == ticket

    def test_re_convert_after_ticket_deleted_recovers(self, tenant, admin_user):
        """The deliberate recovery carve-out: a CONVERTED row whose ticket was
        hard-deleted (converted_ticket is SET_NULL) may be converted again."""
        from apps.inbox_hub.models import HubEmail
        from apps.inbox_hub.services import convert_to_ticket

        with tenant_context(tenant):
            TicketStatusFactory(tenant=tenant, is_default=True, name="Open", slug="open")
            contact = ContactFactory(tenant=tenant, email="customer@example.com")
            hub = _hub(
                tenant, state=HubEmail.State.CONVERTED_TO_TICKET,
                contact=contact, converted_ticket=None,
            )
            ticket = convert_to_ticket(hub, actor=admin_user)
        assert ticket is not None
        hub.refresh_from_db()
        assert hub.converted_ticket_id == ticket.pk
        assert hub.state == HubEmail.State.CONVERTED_TO_TICKET

    def test_stale_instance_convert_rejected(self, tenant, admin_user):
        """The terminal guard reads the row under a lock, not the caller's
        stale in-memory state — a convert racing a committed dismiss loses."""
        from apps.inbox_hub.models import HubEmail
        from apps.inbox_hub.services import convert_to_ticket

        hub = _hub(tenant, state=HubEmail.State.NEW)  # stale in-memory NEW
        HubEmail.unscoped.filter(pk=hub.pk).update(state=HubEmail.State.DISMISSED)
        with tenant_context(tenant), pytest.raises(ValueError):
            convert_to_ticket(hub, actor=admin_user)

    def test_stale_instance_dismiss_rejected(self, tenant, admin_user):
        from apps.inbox_hub.models import HubEmail
        from apps.inbox_hub.services import dismiss_hub_email

        hub = _hub(tenant, state=HubEmail.State.NEW)  # stale in-memory NEW
        HubEmail.unscoped.filter(pk=hub.pk).update(
            state=HubEmail.State.CONVERTED_TO_TICKET,
        )
        with tenant_context(tenant), pytest.raises(ValueError):
            dismiss_hub_email(hub, actor=admin_user, reason="oops")

    def test_inbox_create_ticket_on_dismissed_hub_email_returns_400(
        self, admin_client, tenant
    ):
        """The SECOND convert_to_ticket caller — the personal Inbox page's
        create-ticket action — must also surface the terminal guard as a 400,
        not a 500."""
        from apps.inbox_hub.models import HubEmail

        hub = _hub(tenant, state=HubEmail.State.DISMISSED)
        resp = admin_client.post(
            f"/api/v1/inbound-email/{hub.inbound_id}/create-ticket/", {},
        )
        assert resp.status_code == 400
        hub.refresh_from_db()
        assert hub.state == HubEmail.State.DISMISSED


@pytest.mark.django_db
class TestSlaRiskLens:
    def test_lens_mirrors_sweep_semantics(self, admin_client, tenant, agent_user):
        """?sla_risk=true = active + deadline set + un-breached + un-responded.
        Responded and terminal rows never show (the sweep will never act on
        them, so surfacing them as 'at risk' would be a lie)."""
        from apps.inbox_hub.models import HubEmail

        due_soon = timezone.now() + timedelta(hours=1)
        at_risk = _hub(tenant, state=HubEmail.State.NEW, sla_response_due_at=due_soon)
        responded = _hub(
            tenant, state=HubEmail.State.ASSIGNED, assignee=agent_user,
            sla_response_due_at=due_soon, first_responded_at=timezone.now(),
        )
        converted = _hub(
            tenant, state=HubEmail.State.CONVERTED_TO_TICKET,
            sla_response_due_at=due_soon,
        )

        resp = admin_client.get("/api/v1/inbox-hub/hub-emails/?sla_risk=true")
        assert resp.status_code == 200
        data = resp.json()
        rows = data.get("results", data)
        ids = {row["id"] for row in rows}
        assert str(at_risk.id) in ids
        assert str(responded.id) not in ids
        assert str(converted.id) not in ids
