"""Regression tests for the QA-audit follow-up fixes (2026-06-18).

These pin the behaviour changes made in response to the multi-agent audit of
"lacking" sections. Each test class maps to one fix:

* TestCommentMentionTenantScoping   -- comment @mentions no longer resolve
  cross-tenant users (the Sprint-0 messaging fix, finally applied to comments).
* TestTicketAssigneeMembership       -- ticket CREATE rejects a non-member /
  cross-tenant assignee (Ticket.clean() is never called by save()).
* TestInboundFailedPersistence       -- a permanently failing inbound email is
  now marked FAILED (outside the rolled-back processing transaction) instead of
  silently vanishing.
* TestGrantTempRoleAdminExclusion    -- grant-temp-role refuses the Admin role.
* TestDashboardManagerAnalyticsGate  -- tenant-wide agent/SLA analytics are
  manager-only; agents/viewers get empty sets.
* TestInternalCommentBroadcastRedaction -- internal-comment bodies are stripped
  from the tenant-wide live broadcast.
* TestHubFirstResponded              -- working-state transitions stamp
  first_responded_at, which suppresses the false SLA response breach.
* TestEscalateTerminalNoop           -- escalating a terminal email is a no-op.
* TestEffectiveRoleDrift             -- a temp-promoted agent is treated by its
  effective_role (manager) on the access gates that used to read raw role.
"""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from main.context import tenant_context

from conftest import (
    InboundEmailFactory,
    MembershipFactory,
    TicketFactory,
    make_api_client,
)


def _ctx(tenant, user):
    return {"request": SimpleNamespace(tenant=tenant, user=user)}


# ---------------------------------------------------------------------------
# Comment @mention cross-tenant scoping
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestCommentMentionTenantScoping:
    def _make_comment(self, tenant, author, body):
        from apps.comments.serializers import CommentCreateSerializer

        with tenant_context(tenant):
            ticket = TicketFactory(tenant=tenant)
            s = CommentCreateSerializer(
                data={
                    "content_type": "tickets.ticket",
                    "object_id": str(ticket.id),
                    "body": body,
                },
                context=_ctx(tenant, author),
            )
            assert s.is_valid(), s.errors
            return s.save()

    def test_cross_tenant_mention_creates_no_mention_row(
        self, tenant, tenant_b, admin_user
    ):
        from apps.comments.models import Mention

        foreign = MembershipFactory(tenant=tenant_b).user
        comment = self._make_comment(
            tenant, admin_user, f"hi @[Foreigner](user:{foreign.id})"
        )
        assert not Mention.objects.filter(
            comment=comment, mentioned_user=foreign
        ).exists()

    def test_same_tenant_mention_creates_mention_row(self, tenant, admin_user):
        from apps.comments.models import Mention

        mate = MembershipFactory(tenant=tenant).user
        comment = self._make_comment(
            tenant, admin_user, f"hi @[Mate](user:{mate.id})"
        )
        assert Mention.objects.filter(
            comment=comment, mentioned_user=mate
        ).exists()


# ---------------------------------------------------------------------------
# Ticket creation assignee membership
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestTicketAssigneeMembership:
    def _serializer(self, tenant, user, assignee, default_status):
        from apps.tickets.serializers import TicketCreateSerializer

        return TicketCreateSerializer(
            data={
                "subject": "Need help",
                "description": "Please assist.",
                "status": str(default_status.id),
                "assignee": str(assignee.id),
            },
            context=_ctx(tenant, user),
        )

    def test_cross_tenant_assignee_rejected(
        self, tenant, tenant_b, admin_user, default_status
    ):
        foreign = MembershipFactory(tenant=tenant_b).user
        with tenant_context(tenant):
            s = self._serializer(tenant, admin_user, foreign, default_status)
            assert not s.is_valid()
            assert "assignee" in s.errors

    def test_same_tenant_assignee_allowed(
        self, tenant, admin_user, default_status
    ):
        mate = MembershipFactory(tenant=tenant).user
        with tenant_context(tenant):
            s = self._serializer(tenant, admin_user, mate, default_status)
            assert s.is_valid(), s.errors


# ---------------------------------------------------------------------------
# Inbound-email FAILED persistence (rollback bug)
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestInboundFailedPersistence:
    def test_mark_inbound_failed_persists(self, tenant):
        from apps.inbound_email.models import InboundEmail
        from apps.inbound_email.services import mark_inbound_failed

        inbound = InboundEmailFactory(
            tenant=tenant, status=InboundEmail.Status.PROCESSING
        )
        mark_inbound_failed(str(inbound.id), "boom-message")
        inbound.refresh_from_db()
        assert inbound.status == InboundEmail.Status.FAILED
        assert inbound.error_message == "boom-message"

    def test_task_marks_failed_when_retries_exhausted(self, tenant, monkeypatch):
        """On the final attempt the task must persist FAILED, not silently give
        up. Before the fix the FAILED write lived inside the @transaction.atomic
        body and was rolled back by the re-raise, so the email never showed
        FAILED at all."""
        from apps.inbound_email import services, tasks
        from apps.inbound_email.models import InboundEmail

        inbound = InboundEmailFactory(
            tenant=tenant, status=InboundEmail.Status.PENDING
        )

        def _boom(_id):
            raise RuntimeError("kaboom")

        monkeypatch.setattr(services, "process_inbound_email", _boom)

        # retries == max_retries -> the "retries exhausted" branch fires.
        result = tasks.process_inbound_email_task.apply(
            args=[str(inbound.id)], retries=tasks.process_inbound_email_task.max_retries
        )
        assert result.successful()  # returned cleanly, did not raise
        inbound.refresh_from_db()
        assert inbound.status == InboundEmail.Status.FAILED
        assert "kaboom" in inbound.error_message


# ---------------------------------------------------------------------------
# grant-temp-role admin exclusion
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestGrantTempRoleAdminExclusion:
    URL = "/api/v1/agents/agents/grant-temp-role/"

    def test_granting_admin_role_is_rejected(
        self, tenant, admin_user, agent_user, admin_role
    ):
        client = make_api_client(admin_user, tenant)
        resp = client.post(
            self.URL,
            {
                "user_id": str(agent_user.id),
                "role_id": str(admin_role.id),
                "duration_hours": 8,
            },
            format="json",
        )
        assert resp.status_code == 400
        assert "Admin role" in str(resp.data)

    def test_granting_manager_role_is_allowed(
        self, tenant, admin_user, agent_user, manager_role
    ):
        client = make_api_client(admin_user, tenant)
        resp = client.post(
            self.URL,
            {
                "user_id": str(agent_user.id),
                "role_id": str(manager_role.id),
                "duration_hours": 8,
            },
            format="json",
        )
        assert resp.status_code in (200, 201, 204)


# ---------------------------------------------------------------------------
# Dashboard manager-only analytics gate
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestDashboardManagerAnalyticsGate:
    URL = "/api/v1/analytics/dashboard/"

    def test_agent_does_not_receive_tenant_wide_analytics(
        self, tenant, agent_user
    ):
        client = make_api_client(agent_user, tenant)
        resp = client.get(self.URL)
        assert resp.status_code == 200
        assert resp.data["is_admin_or_manager"] is False
        # Empty-but-shaped: the manager-only figures are withheld.
        assert resp.data["agent_performance"] == {"agents": []}
        assert resp.data["sla_compliance"] == {"policies": []}

    def test_manager_is_flagged_admin_or_manager(self, tenant, manager_user):
        client = make_api_client(manager_user, tenant)
        resp = client.get(self.URL)
        assert resp.status_code == 200
        assert resp.data["is_admin_or_manager"] is True
        # The manager path runs the real aggregators (dicts, possibly empty).
        assert "agents" in resp.data["agent_performance"]
        assert "policies" in resp.data["sla_compliance"]


# ---------------------------------------------------------------------------
# Internal-comment broadcast redaction
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestInternalCommentBroadcastRedaction:
    def _ct(self):
        from django.contrib.contenttypes.models import ContentType

        from apps.comments.models import Comment

        return ContentType.objects.get_for_model(Comment)

    def test_internal_body_is_redacted(self):
        import uuid

        from apps.comments.models import Comment
        from apps.comments.signals import _serialise_comment

        internal = Comment(
            body="secret internal note", is_internal=True,
            content_type=self._ct(), object_id=uuid.uuid4(),
        )
        payload = _serialise_comment(internal)
        assert payload["is_internal"] is True
        assert payload["body"] is None

    def test_public_body_is_preserved(self):
        import uuid

        from apps.comments.models import Comment
        from apps.comments.signals import _serialise_comment

        public = Comment(
            body="hello customer", is_internal=False,
            content_type=self._ct(), object_id=uuid.uuid4(),
        )
        payload = _serialise_comment(public)
        assert payload["body"] == "hello customer"


# ---------------------------------------------------------------------------
# Inbox-Hub first_responded_at + SLA breach interaction
# ---------------------------------------------------------------------------
def _build_hub_email(tenant, *, state, assignee=None, sla_response_due_at=None,
                     first_responded_at=None):
    import uuid

    from apps.inbound_email.models import InboundEmail
    from apps.inbox_hub.models import HubEmail

    inbound = InboundEmail.objects.create(
        tenant=tenant,
        sender_email="customer@example.com",
        recipient_email=f"support+{tenant.slug}@kanzan.io",
        subject="Hello",
        body_text="Body",
        message_id=f"{uuid.uuid4()}@test.com",
        raw_headers="",
    )
    return HubEmail.unscoped.create(
        tenant=tenant,
        inbound=inbound,
        state=state,
        priority=HubEmail.Priority.NORMAL,
        assignee=assignee,
        sla_response_due_at=sla_response_due_at,
        first_responded_at=first_responded_at,
    )


@pytest.mark.django_db
class TestHubFirstResponded:
    def test_transition_to_working_state_stamps_first_responded_at(
        self, tenant, agent_user
    ):
        from apps.inbox_hub.models import HubEmail
        from apps.inbox_hub.services import transition_hub_email

        hub = _build_hub_email(
            tenant, state=HubEmail.State.ASSIGNED, assignee=agent_user
        )
        assert hub.first_responded_at is None
        with tenant_context(tenant):
            transition_hub_email(hub, HubEmail.State.IN_PROGRESS, actor=agent_user)
        hub.refresh_from_db()
        assert hub.first_responded_at is not None

    def test_responded_email_does_not_breach(self, tenant, agent_user):
        from apps.inbox_hub.models import HubEmail
        from apps.inbox_hub.tasks import check_hub_sla_breaches

        hub = _build_hub_email(
            tenant,
            state=HubEmail.State.IN_PROGRESS,
            assignee=agent_user,
            sla_response_due_at=timezone.now() - timedelta(minutes=5),
            first_responded_at=timezone.now() - timedelta(minutes=1),
        )
        check_hub_sla_breaches()
        hub.refresh_from_db()
        assert hub.response_breached is False

    def test_unresponded_overdue_email_breaches(self, tenant, agent_user):
        from apps.inbox_hub.models import HubEmail
        from apps.inbox_hub.tasks import check_hub_sla_breaches

        hub = _build_hub_email(
            tenant,
            state=HubEmail.State.ASSIGNED,
            assignee=agent_user,
            sla_response_due_at=timezone.now() - timedelta(minutes=5),
        )
        check_hub_sla_breaches()
        hub.refresh_from_db()
        assert hub.response_breached is True


@pytest.mark.django_db
class TestEscalateTerminalNoop:
    def test_escalate_on_dismissed_is_noop(self, tenant, agent_user):
        from apps.inbox_hub.models import HubEmail
        from apps.inbox_hub.services import escalate_hub_email

        hub = _build_hub_email(tenant, state=HubEmail.State.DISMISSED)
        with tenant_context(tenant):
            escalate_hub_email(hub, actor=agent_user, reason="late")
        hub.refresh_from_db()
        assert hub.escalation_count == 0
        assert hub.state == HubEmail.State.DISMISSED

    def test_escalate_on_assigned_increments(self, tenant, agent_user):
        from apps.inbox_hub.models import HubEmail
        from apps.inbox_hub.services import escalate_hub_email

        hub = _build_hub_email(
            tenant, state=HubEmail.State.ASSIGNED, assignee=agent_user
        )
        with tenant_context(tenant):
            escalate_hub_email(hub, actor=agent_user, reason="late")
        hub.refresh_from_db()
        assert hub.escalation_count == 1
        assert hub.state == HubEmail.State.ESCALATED


# ---------------------------------------------------------------------------
# effective_role drift -- temp-promoted agent treated as manager
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestEffectiveRoleDrift:
    def test_temp_promoted_agent_is_manager_on_dashboard(
        self, tenant, agent_user, manager_role
    ):
        from apps.accounts.models import TenantMembership

        membership = TenantMembership.objects.get(user=agent_user, tenant=tenant)
        membership.temporary_role = manager_role
        membership.temporary_role_expires_at = timezone.now() + timedelta(hours=2)
        membership.save(update_fields=[
            "temporary_role", "temporary_role_expires_at",
        ])

        client = make_api_client(agent_user, tenant)
        resp = client.get("/api/v1/analytics/dashboard/")
        assert resp.status_code == 200
        # Raw role is agent (level 30 -> False); effective_role is manager
        # (level 20 -> True). This passing proves the gate reads effective_role.
        assert resp.data["is_admin_or_manager"] is True
