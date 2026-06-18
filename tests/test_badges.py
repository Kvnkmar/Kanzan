"""
Module 14 — Nav Badge Counts (12 tests)

Covers: GET /api/v1/nav/badge-counts/ response structure, count accuracy
for tickets/emails/calendar/messages, capping at 99, tenant isolation,
and role-scoped counts.
"""

import uuid
from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.utils import timezone
from freezegun import freeze_time

from apps.inbound_email.models import InboundEmail
from apps.tickets.models import Ticket, TicketStatus
from main.context import set_current_tenant

from tests.base import KanzenBaseTestCase


BADGE_URL = "/api/v1/nav/badge-counts/"


class BadgeCountBaseTests(KanzenBaseTestCase):
    """Tests for the sidebar badge-count endpoint."""

    def setUp(self):
        super().setUp()
        self.set_tenant(self.tenant_a)
        self.auth_tenant(self.admin_a, self.tenant_a)

    def _get_badges(self):
        return self.client.get(BADGE_URL)

    def _create_inbound_email(self, tenant, inbox_status="pending", **kwargs):
        """Helper to create an InboundEmail record."""
        defaults = {
            "tenant": tenant,
            "message_id": f"{uuid.uuid4()}@test.com",
            "sender_email": "sender@example.com",
            "recipient_email": "support@example.com",
            "subject": "Test email",
            "body_text": "Test body",
            "inbox_status": inbox_status,
        }
        defaults.update(kwargs)
        return InboundEmail.objects.create(**defaults)

    # 14.1 Returns all 4 keys
    def test_14_01_returns_all_four_keys(self):
        resp = self._get_badges()
        self.assertEqual(resp.status_code, 200)
        for key in ("tickets", "calendar", "messages", "emails"):
            self.assertIn(key, resp.data, f"Missing key: {key}")

    # 14.2 tickets count = active (open + in-progress + waiting), not resolved/closed
    def test_14_02_tickets_count_active_includes_waiting(self):
        # One ticket in each of the five default statuses.
        self.create_ticket(status=self.status_open_a)
        self.create_ticket(status=self.status_in_progress_a)
        self.create_ticket(status=self.status_waiting_a)
        self.create_ticket(status=self.status_resolved_a)
        self.create_ticket(status=self.status_closed_a)

        resp = self._get_badges()
        self.assertEqual(resp.status_code, 200)
        # The badge counts the active (non-done) statuses the user sees summed
        # on the ticket list: open + in-progress + waiting = 3. Resolved and
        # closed are terminal "done" states and must NOT be counted.
        self.assertEqual(resp.data["tickets"], 3)

    # 14.3 emails count = pending + linked emails in the user's personal inbox
    def test_14_03_emails_count_pending_and_linked(self):
        # Assigned to the requesting user (admin_a) → counts toward the badge,
        # but only the pending/linked (unactioned) ones.
        self._create_inbound_email(self.tenant_a, inbox_status="pending", assignee=self.admin_a)
        self._create_inbound_email(self.tenant_a, inbox_status="linked", assignee=self.admin_a)
        self._create_inbound_email(self.tenant_a, inbox_status="actioned", assignee=self.admin_a)
        self._create_inbound_email(self.tenant_a, inbox_status="ignored", assignee=self.admin_a)
        # Pending but assigned to someone else → NOT in admin_a's personal inbox.
        self._create_inbound_email(self.tenant_a, inbox_status="pending", assignee=self.agent_a)

        resp = self._get_badges()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["emails"], 2)

    # 14.4 calendar count = activities due today or overdue, not completed
    def test_14_04_calendar_count(self):
        """Calendar count depends on CRM Activity model; skip if not available."""
        try:
            from apps.crm.models import Activity
        except ImportError:
            self.skipTest("CRM Activity model not available")

        now = timezone.now()

        # Overdue, incomplete activity assigned to current user
        Activity.objects.create(
            activity_type="task",
            subject="Overdue task",
            due_at=now - timedelta(hours=2),
            assigned_to=self.admin_a,
            created_by=self.admin_a,
            tenant=self.tenant_a,
        )
        # Due today, incomplete
        Activity.objects.create(
            activity_type="call",
            subject="Due today",
            due_at=now + timedelta(hours=1),
            assigned_to=self.admin_a,
            created_by=self.admin_a,
            tenant=self.tenant_a,
        )
        # Completed (should not count)
        Activity.objects.create(
            activity_type="meeting",
            subject="Done meeting",
            due_at=now - timedelta(hours=1),
            completed_at=now,
            assigned_to=self.admin_a,
            created_by=self.admin_a,
            tenant=self.tenant_a,
        )
        # Due tomorrow (should not count)
        Activity.objects.create(
            activity_type="email",
            subject="Future task",
            due_at=now + timedelta(days=2),
            assigned_to=self.admin_a,
            created_by=self.admin_a,
            tenant=self.tenant_a,
        )

        resp = self._get_badges()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["calendar"], 2)

    # 14.5 messages count = unread CHAT messages in the user's conversations
    # (the "messages" badge was repurposed from unread-comments to unread-chat).
    def test_14_05_messages_count_unread_chat(self):
        from apps.messaging.models import (
            Conversation,
            ConversationParticipant,
            ConversationType,
            Message,
        )

        self.set_tenant(self.tenant_a)
        conv = Conversation.objects.create(type=ConversationType.GROUP, name="Team")
        ConversationParticipant.objects.create(conversation=conv, user=self.admin_a)
        ConversationParticipant.objects.create(conversation=conv, user=self.agent_a)

        # Message from another participant → counts as unread for admin_a.
        Message.objects.create(conversation=conv, author=self.agent_a, body="hello")
        # admin_a's own message → excluded.
        Message.objects.create(conversation=conv, author=self.admin_a, body="hi back")

        resp = self._get_badges()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["messages"], 1)

    # 14.6 All counts are integers, never null
    def test_14_06_counts_are_integers_never_null(self):
        resp = self._get_badges()
        self.assertEqual(resp.status_code, 200)
        for key in ("tickets", "calendar", "messages", "emails"):
            val = resp.data[key]
            self.assertIsNotNone(val, f"{key} should not be null")
            self.assertIsInstance(val, int, f"{key} should be int, got {type(val)}")

    # 14.7 Count returns 99 when actual count > 99
    def test_14_07_count_capped_at_99(self):
        # Create 101 open tickets
        tickets = [
            Ticket(
                subject=f"Ticket {i}",
                description="Bulk",
                status=self.status_open_a,
                created_by=self.admin_a,
                tenant=self.tenant_a,
            )
            for i in range(101)
        ]
        # Use bulk_create — numbers are assigned in save() so we need
        # to set them manually for bulk creation
        for i, t in enumerate(tickets, start=9000):
            t.number = i
        Ticket.unscoped.bulk_create(tickets)

        resp = self._get_badges()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["tickets"], 99)

    # 14.8 Actioning an email → emails badge count decreases
    def test_14_08_actioning_email_decreases_count(self):
        email = self._create_inbound_email(
            self.tenant_a, inbox_status="pending", assignee=self.admin_a,
        )

        resp = self._get_badges()
        initial_count = resp.data["emails"]
        self.assertGreaterEqual(initial_count, 1)

        # Action the email (change from pending to actioned)
        email.inbox_status = InboundEmail.InboxStatus.ACTIONED
        email.save(update_fields=["inbox_status"])

        resp = self._get_badges()
        self.assertEqual(resp.data["emails"], initial_count - 1)

    # 14.9 Opening a conversation (marking chat read) → messages badge decreases
    def test_14_09_marking_chat_read_decreases_count(self):
        from django.utils import timezone

        from apps.messaging.models import (
            Conversation,
            ConversationParticipant,
            ConversationType,
            Message,
        )

        self.set_tenant(self.tenant_a)
        conv = Conversation.objects.create(type=ConversationType.GROUP, name="Team")
        participant = ConversationParticipant.objects.create(
            conversation=conv, user=self.admin_a
        )
        ConversationParticipant.objects.create(conversation=conv, user=self.agent_a)
        Message.objects.create(conversation=conv, author=self.agent_a, body="ping")

        resp = self._get_badges()
        self.assertEqual(resp.data["messages"], 1)

        # Mark the conversation read for admin_a.
        participant.last_read_at = timezone.now()
        participant.save(update_fields=["last_read_at"])

        resp = self._get_badges()
        self.assertEqual(resp.data["messages"], 0)

    # 14.10 Completing an activity → calendar badge count decreases
    def test_14_10_completing_activity_decreases_calendar(self):
        try:
            from apps.crm.models import Activity
        except ImportError:
            self.skipTest("CRM Activity model not available")

        now = timezone.now()
        activity = Activity.objects.create(
            activity_type="task",
            subject="Due task",
            due_at=now - timedelta(hours=1),
            assigned_to=self.admin_a,
            created_by=self.admin_a,
            tenant=self.tenant_a,
        )

        resp = self._get_badges()
        initial_cal = resp.data["calendar"]
        self.assertGreaterEqual(initial_cal, 1)

        activity.completed_at = now
        activity.save(update_fields=["completed_at"])

        resp = self._get_badges()
        self.assertEqual(resp.data["calendar"], initial_cal - 1)


class BadgeTenantIsolationTests(KanzenBaseTestCase):
    """Tests for tenant scoping and role-based badge counts."""

    def setUp(self):
        super().setUp()

    def _get_badges(self):
        return self.client.get(BADGE_URL)

    # 14.11 Counts are tenant-scoped
    def test_14_11_counts_tenant_scoped(self):
        # Create tickets in tenant A
        self.set_tenant(self.tenant_a)
        self.create_ticket(status=self.status_open_a)
        self.create_ticket(status=self.status_open_a)

        # Create ticket in tenant B
        self.set_tenant(self.tenant_b)
        status_open_b = TicketStatus.unscoped.get(
            tenant=self.tenant_b, is_default=True,
        )
        self.create_ticket(
            tenant=self.tenant_b,
            user=self.admin_b,
            status=status_open_b,
        )

        # Tenant A should see only their tickets
        self.auth_tenant(self.admin_a, self.tenant_a)
        resp = self._get_badges()
        count_a = resp.data["tickets"]

        # Tenant B should see only their tickets
        self.auth_tenant(self.admin_b, self.tenant_b)
        resp = self._get_badges()
        count_b = resp.data["tickets"]

        # They should differ (A has 2, B has 1)
        self.assertGreaterEqual(count_a, 2)
        self.assertEqual(count_b, 1)

    # 14.12 Agent gets role-scoped counts (own tickets only)
    def test_14_12_agent_gets_role_scoped_counts(self):
        self.set_tenant(self.tenant_a)

        # Ticket assigned to agent
        self.create_ticket(
            status=self.status_open_a,
            assignee=self.agent_a,
        )
        # Ticket created by agent (no assignee)
        self.create_ticket(
            status=self.status_open_a,
            user=self.agent_a,
        )
        # Ticket NOT related to agent at all
        self.create_ticket(
            status=self.status_open_a,
            assignee=self.admin_a,
        )

        # Admin sees all open tickets
        self.auth_tenant(self.admin_a, self.tenant_a)
        resp = self._get_badges()
        admin_ticket_count = resp.data["tickets"]

        # Agent should see fewer tickets (only own)
        self.auth_tenant(self.agent_a, self.tenant_a)
        resp = self._get_badges()
        agent_ticket_count = resp.data["tickets"]

        self.assertGreater(admin_ticket_count, agent_ticket_count)
        # Agent should see at least the 2 tickets they're related to
        self.assertGreaterEqual(agent_ticket_count, 2)
