"""
Phase 4 & 5 — Closure and Post-closure tests.

Covers:
- Auto-close timer: ticket auto-closes after resolved period
- Reopen cancels auto-close: customer reply reopens resolved ticket
- CSAT token validation: signed tokens, expired tokens, double-submit
- KB suggestion flag: set when category has < 3 published KB articles
- Resolution breach flag: set at closure when sla_resolution_due is exceeded
"""

import datetime
from unittest.mock import patch

import pytest
from django.core import signing
from django.utils import timezone

from apps.accounts.models import Role
from conftest import (
    ContactFactory,
    MembershipFactory,
    TicketFactory,
    TicketStatusFactory,
    UserFactory,
)
from main.context import clear_current_tenant, set_current_tenant


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_statuses(tenant):
    """Create the standard status set with correct Phase 4 semantics."""
    return {
        "open": TicketStatusFactory(
            tenant=tenant, name="Open", slug="open",
            order=10, is_default=True,
        ),
        "in-progress": TicketStatusFactory(
            tenant=tenant, name="In Progress", slug="in-progress",
            order=20,
        ),
        "waiting": TicketStatusFactory(
            tenant=tenant, name="Waiting", slug="waiting",
            order=30, pauses_sla=True,
        ),
        "resolved": TicketStatusFactory(
            tenant=tenant, name="Resolved", slug="resolved",
            order=40, is_closed=False,  # Phase 4: NOT closed
        ),
        "closed": TicketStatusFactory(
            tenant=tenant, name="Closed", slug="closed",
            order=50, is_closed=True,
        ),
    }


def _make_ticket(tenant, status, created_by, **kwargs):
    set_current_tenant(tenant)
    ticket = TicketFactory(
        tenant=tenant, status=status, created_by=created_by, **kwargs,
    )
    clear_current_tenant()
    return ticket


# ===========================================================================
# 1. AUTO-CLOSE TIMER
# ===========================================================================


@pytest.mark.django_db(transaction=True)
class TestAutoCloseTimer:
    """Verify solved→closed auto-close scheduling and execution."""

    def test_resolved_sets_solved_at_and_schedules_task(self, tenant, admin_user):
        """Moving to resolved sets solved_at and stores auto_close_task_id."""
        statuses = _make_statuses(tenant)
        ticket = _make_ticket(tenant, statuses["open"], admin_user)

        from apps.tickets.services import transition_ticket_status

        set_current_tenant(tenant)
        transition_ticket_status(ticket, statuses["resolved"], admin_user)
        clear_current_tenant()

        ticket.refresh_from_db()
        assert ticket.solved_at is not None
        assert ticket.auto_close_task_id is not None

    def test_auto_close_task_closes_resolved_ticket(self, tenant, admin_user):
        """The auto_close_ticket task moves a resolved ticket to closed."""
        statuses = _make_statuses(tenant)
        ticket = _make_ticket(tenant, statuses["resolved"], admin_user)

        task_id = "test-auto-close-id"
        from apps.tickets.models import Ticket

        Ticket.unscoped.filter(pk=ticket.pk).update(auto_close_task_id=task_id)

        from apps.tickets.tasks import auto_close_ticket

        # Apply with a specific task_id so self.request.id matches
        auto_close_ticket.apply(
            args=[str(ticket.pk)],
            task_id=task_id,
        )

        ticket.refresh_from_db()
        assert ticket.status.is_closed is True
        assert ticket.closed_at is not None

    def test_auto_close_task_skips_if_not_resolved(self, tenant, admin_user):
        """Auto-close task is a no-op if ticket is no longer in resolved status."""
        statuses = _make_statuses(tenant)
        ticket = _make_ticket(tenant, statuses["open"], admin_user)

        task_id = "some-id"
        from apps.tickets.models import Ticket

        Ticket.unscoped.filter(pk=ticket.pk).update(auto_close_task_id=task_id)

        from apps.tickets.tasks import auto_close_ticket

        auto_close_ticket.apply(args=[str(ticket.pk)], task_id=task_id)

        ticket.refresh_from_db()
        assert ticket.status.slug == "open"

    def test_auto_close_task_skips_on_id_mismatch(self, tenant, admin_user):
        """Auto-close task is a no-op if task ID doesn't match (stale task)."""
        statuses = _make_statuses(tenant)
        ticket = _make_ticket(tenant, statuses["resolved"], admin_user)

        from apps.tickets.models import Ticket

        Ticket.unscoped.filter(pk=ticket.pk).update(
            auto_close_task_id="correct-task-id",
        )

        from apps.tickets.tasks import auto_close_ticket

        # Run with a DIFFERENT task_id — should be rejected by idempotency guard
        auto_close_ticket.apply(
            args=[str(ticket.pk)],
            task_id="wrong-task-id",
        )

        ticket.refresh_from_db()
        assert ticket.status.slug == "resolved"  # Not closed

    def test_reopen_clears_auto_close_fields(self, tenant, admin_user):
        """Reopening from resolved clears solved_at and auto_close_task_id."""
        statuses = _make_statuses(tenant)

        set_current_tenant(tenant)
        ticket = TicketFactory(
            tenant=tenant, status=statuses["resolved"],
            created_by=admin_user,
            solved_at=timezone.now(),
            auto_close_task_id="task-to-cancel",
        )

        from apps.tickets.services import transition_ticket_status

        transition_ticket_status(ticket, statuses["open"], admin_user)
        clear_current_tenant()

        ticket.refresh_from_db()
        assert ticket.status.slug == "open"
        assert ticket.solved_at is None
        assert ticket.auto_close_task_id is None


# ===========================================================================
# 2. REOPEN ON CUSTOMER REPLY
# ===========================================================================


@pytest.mark.django_db(transaction=True)
class TestReopenOnReply:
    """Verify that customer reply reopens a resolved ticket."""

    def test_reply_reopens_resolved_ticket(self, tenant, admin_user):
        """Inbound reply to a resolved ticket reopens it to 'open'."""
        statuses = _make_statuses(tenant)

        set_current_tenant(tenant)
        contact = ContactFactory(tenant=tenant, email="requester@test.com")
        ticket = TicketFactory(
            tenant=tenant, status=statuses["resolved"],
            created_by=admin_user, contact=contact,
            solved_at=timezone.now(),
            auto_close_task_id="some-task-id",
        )
        clear_current_tenant()

        from apps.inbound_email.services import _reopen_resolved_ticket
        from apps.inbound_email.services import get_system_user

        set_current_tenant(tenant)
        system_user = get_system_user(tenant)
        _reopen_resolved_ticket(ticket, system_user)
        clear_current_tenant()

        ticket.refresh_from_db()
        assert ticket.status.slug == "open"
        assert ticket.solved_at is None
        assert ticket.auto_close_task_id is None


# ===========================================================================
# 3. CSAT TOKEN VALIDATION
# ===========================================================================


@pytest.mark.django_db(transaction=True)
class TestCSATSubmission:
    """Verify CSAT token signing, validation, and submission."""

    def _make_token(self, ticket, tenant):
        return signing.dumps(
            {"t": str(ticket.pk), "n": str(tenant.pk)},
            salt="csat",
        )

    def test_valid_csat_submission(self, tenant, admin_user):
        """A valid signed token with rating 1-5 is accepted."""
        statuses = _make_statuses(tenant)
        ticket = _make_ticket(tenant, statuses["resolved"], admin_user)

        token = self._make_token(ticket, tenant)

        from rest_framework.test import APIClient

        client = APIClient()
        resp = client.post(
            "/api/v1/tickets/csat/",
            {"token": token, "rating": 5, "comment": "Excellent!"},
            format="json",
            HTTP_HOST=f"{tenant.slug}.localhost:8001",
        )
        assert resp.status_code == 200, f"Got {resp.status_code}: {resp.data}"

        ticket.refresh_from_db()
        assert ticket.csat_rating == 5
        assert ticket.csat_comment == "Excellent!"
        assert ticket.csat_submitted_at is not None

    def test_invalid_token_rejected(self, tenant, admin_user):
        """A tampered token is rejected with 400."""
        from rest_framework.test import APIClient

        client = APIClient()
        resp = client.post(
            "/api/v1/tickets/csat/",
            {"token": "tampered-garbage", "rating": 3},
            format="json",
            HTTP_HOST=f"{tenant.slug}.localhost:8001",
        )
        assert resp.status_code == 400
        assert "Invalid" in resp.data["detail"]

    def test_expired_token_rejected(self, tenant, admin_user):
        """An expired token (> 12 days) is rejected."""
        statuses = _make_statuses(tenant)
        ticket = _make_ticket(tenant, statuses["resolved"], admin_user)

        # Create a token that appears to have been signed 13 days ago
        token = signing.dumps(
            {"t": str(ticket.pk), "n": str(tenant.pk)},
            salt="csat",
        )

        from rest_framework.test import APIClient

        client = APIClient()

        # Patch time to be 13 days in the future so the token appears expired
        future = timezone.now() + datetime.timedelta(days=13)
        with patch("django.core.signing.time.time", return_value=future.timestamp()):
            resp = client.post(
                "/api/v1/tickets/csat/",
                {"token": token, "rating": 4},
                format="json",
                HTTP_HOST=f"{tenant.slug}.localhost:8001",
            )
        assert resp.status_code == 400

    def test_double_submit_rejected(self, tenant, admin_user):
        """Second CSAT submission for the same ticket returns 409."""
        statuses = _make_statuses(tenant)
        ticket = _make_ticket(tenant, statuses["resolved"], admin_user)

        token = self._make_token(ticket, tenant)

        from rest_framework.test import APIClient

        client = APIClient()
        host = f"{tenant.slug}.localhost:8001"

        # First submission
        resp1 = client.post(
            "/api/v1/tickets/csat/",
            {"token": token, "rating": 4},
            format="json",
            HTTP_HOST=host,
        )
        assert resp1.status_code == 200

        # Second submission
        resp2 = client.post(
            "/api/v1/tickets/csat/",
            {"token": token, "rating": 2},
            format="json",
            HTTP_HOST=host,
        )
        assert resp2.status_code == 409

        # Rating should be from the first submission
        ticket.refresh_from_db()
        assert ticket.csat_rating == 4

    def test_invalid_rating_rejected(self, tenant, admin_user):
        """Rating outside 1-5 is rejected."""
        statuses = _make_statuses(tenant)
        ticket = _make_ticket(tenant, statuses["resolved"], admin_user)

        token = self._make_token(ticket, tenant)

        from rest_framework.test import APIClient

        client = APIClient()
        resp = client.post(
            "/api/v1/tickets/csat/",
            {"token": token, "rating": 6},
            format="json",
            HTTP_HOST=f"{tenant.slug}.localhost:8001",
        )
        assert resp.status_code == 400


# ===========================================================================
# 4. KB SUGGESTION FLAG
# ===========================================================================


@pytest.mark.django_db(transaction=True)
class TestKBSuggestionFlag:
    """Verify that ticket_closed sets needs_kb_article when < 3 articles."""

    def test_flag_set_when_few_articles(self, tenant, admin_user):
        """Ticket with a category that has < 3 published KB articles gets flagged."""
        statuses = _make_statuses(tenant)
        ticket = _make_ticket(
            tenant, statuses["resolved"], admin_user,
            category="Billing",
        )

        # Create 2 published articles in a KB category named "Billing"
        set_current_tenant(tenant)
        from apps.knowledge.models import Article, Category

        kb_cat = Category.objects.create(
            tenant=tenant, name="Billing", slug="billing",
        )
        Article.objects.create(
            tenant=tenant, title="A1", slug="a1", category=kb_cat,
            status="published", author=admin_user,
        )
        Article.objects.create(
            tenant=tenant, title="A2", slug="a2", category=kb_cat,
            status="published", author=admin_user,
        )

        # Close the ticket (triggers ticket_closed → check_kb_article_coverage)
        from apps.tickets.services import transition_ticket_status

        transition_ticket_status(ticket, statuses["closed"], admin_user)
        clear_current_tenant()

        ticket.refresh_from_db()
        assert ticket.needs_kb_article is True

    def test_flag_not_set_when_enough_articles(self, tenant, admin_user):
        """Ticket with a category that has >= 3 articles is NOT flagged."""
        statuses = _make_statuses(tenant)
        ticket = _make_ticket(
            tenant, statuses["resolved"], admin_user,
            category="Technical",
        )

        set_current_tenant(tenant)
        from apps.knowledge.models import Article, Category

        kb_cat = Category.objects.create(
            tenant=tenant, name="Technical", slug="technical",
        )
        for i in range(3):
            Article.objects.create(
                tenant=tenant, title=f"T{i}", slug=f"t{i}", category=kb_cat,
                status="published", author=admin_user,
            )

        from apps.tickets.services import transition_ticket_status

        transition_ticket_status(ticket, statuses["closed"], admin_user)
        clear_current_tenant()

        ticket.refresh_from_db()
        assert ticket.needs_kb_article is False

    def test_flag_not_set_when_no_category(self, tenant, admin_user):
        """Ticket without a category is not flagged."""
        statuses = _make_statuses(tenant)
        ticket = _make_ticket(
            tenant, statuses["resolved"], admin_user,
            category=None,
        )

        set_current_tenant(tenant)
        from apps.tickets.services import transition_ticket_status

        transition_ticket_status(ticket, statuses["closed"], admin_user)
        clear_current_tenant()

        ticket.refresh_from_db()
        assert ticket.needs_kb_article is False

    def test_case_insensitive_category_match(self, tenant, admin_user):
        """KB check uses case-insensitive match on category name."""
        statuses = _make_statuses(tenant)
        ticket = _make_ticket(
            tenant, statuses["resolved"], admin_user,
            category="billing",  # lowercase
        )

        set_current_tenant(tenant)
        from apps.knowledge.models import Article, Category

        kb_cat = Category.objects.create(
            tenant=tenant, name="Billing", slug="billing",  # Title case
        )
        Article.objects.create(
            tenant=tenant, title="A1", slug="a1", category=kb_cat,
            status="published", author=admin_user,
        )

        from apps.tickets.services import transition_ticket_status

        transition_ticket_status(ticket, statuses["closed"], admin_user)
        clear_current_tenant()

        ticket.refresh_from_db()
        # 1 article < 3 → flagged, despite case mismatch
        assert ticket.needs_kb_article is True


# ===========================================================================
# 5. RESOLUTION BREACH FLAG
# ===========================================================================


@pytest.mark.django_db(transaction=True)
class TestResolutionBreach:
    """Verify sla_resolution_breached is set at closure."""

    def test_breach_flag_set_when_late(self, tenant, admin_user):
        """If closed_at > sla_resolution_due, flag is set."""
        statuses = _make_statuses(tenant)
        now = timezone.now()

        # Deadline was 2 hours ago
        ticket = _make_ticket(
            tenant, statuses["resolved"], admin_user,
            sla_resolution_due=now - datetime.timedelta(hours=2),
        )

        from apps.tickets.services import transition_ticket_status

        set_current_tenant(tenant)
        transition_ticket_status(ticket, statuses["closed"], admin_user)
        clear_current_tenant()

        ticket.refresh_from_db()
        assert ticket.sla_resolution_breached is True

    def test_no_breach_when_on_time(self, tenant, admin_user):
        """If closed_at <= sla_resolution_due, flag is NOT set."""
        statuses = _make_statuses(tenant)
        now = timezone.now()

        ticket = _make_ticket(
            tenant, statuses["resolved"], admin_user,
            sla_resolution_due=now + datetime.timedelta(hours=24),
        )

        from apps.tickets.services import transition_ticket_status

        set_current_tenant(tenant)
        transition_ticket_status(ticket, statuses["closed"], admin_user)
        clear_current_tenant()

        ticket.refresh_from_db()
        assert ticket.sla_resolution_breached is False

    def test_no_breach_when_no_sla(self, tenant, admin_user):
        """If no sla_resolution_due, flag stays False."""
        statuses = _make_statuses(tenant)
        ticket = _make_ticket(
            tenant, statuses["resolved"], admin_user,
        )

        from apps.tickets.services import transition_ticket_status

        set_current_tenant(tenant)
        transition_ticket_status(ticket, statuses["closed"], admin_user)
        clear_current_tenant()

        ticket.refresh_from_db()
        assert ticket.sla_resolution_breached is False

    def test_breach_signal_fired(self, tenant, admin_user):
        """sla_resolution_breached signal fires when breach detected at closure."""
        statuses = _make_statuses(tenant)
        now = timezone.now()

        ticket = _make_ticket(
            tenant, statuses["resolved"], admin_user,
            sla_resolution_due=now - datetime.timedelta(hours=1),
        )

        from apps.tickets.signals import sla_resolution_breached

        received = []

        def handler(sender, instance, closed_at, due_at, **kwargs):
            received.append({
                "ticket_id": instance.pk,
                "closed_at": closed_at,
                "due_at": due_at,
            })

        sla_resolution_breached.connect(handler)
        try:
            from apps.tickets.services import transition_ticket_status

            set_current_tenant(tenant)
            transition_ticket_status(ticket, statuses["closed"], admin_user)
            clear_current_tenant()

            assert len(received) == 1
            assert received[0]["ticket_id"] == ticket.pk
        finally:
            sla_resolution_breached.disconnect(handler)

    def test_ticket_closed_signal_fired(self, tenant, admin_user):
        """ticket_closed signal fires with full payload on closure."""
        statuses = _make_statuses(tenant)
        ticket = _make_ticket(
            tenant, statuses["resolved"], admin_user,
            priority="high",
            channel="email",
        )

        from apps.tickets.signals import ticket_closed

        received = []

        def handler(sender, instance, payload, **kwargs):
            received.append(payload)

        ticket_closed.connect(handler)
        try:
            from apps.tickets.services import transition_ticket_status

            set_current_tenant(tenant)
            transition_ticket_status(ticket, statuses["closed"], admin_user)
            clear_current_tenant()

            assert len(received) == 1
            p = received[0]
            assert p["priority"] == "high"
            assert p["channel"] == "email"
            assert p["tenant_id"] == str(tenant.pk)
            assert p["resolution_time_seconds"] > 0
        finally:
            ticket_closed.disconnect(handler)
