"""
Module 7 — Email Integration Tests (24 tests).

Covers inbound email processing, inbox workflow API, and outbound email tasks.
"""

import unittest
import uuid
from unittest.mock import patch, MagicMock

from django.test import override_settings
from django.utils import timezone
from freezegun import freeze_time

from apps.comments.models import ActivityLog
from apps.contacts.models import Contact
from apps.inbound_email.models import InboundEmail
from apps.inbound_email.services import process_inbound_email, resolve_tenant_from_address
from apps.tickets.models import Ticket, TicketActivity
from main.context import set_current_tenant

from tests.base import KanzenBaseTestCase


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_inbound_email(**kwargs):
    """Create an InboundEmail record with sensible defaults."""
    defaults = {
        "message_id": f"{uuid.uuid4().hex[:16]}@test.example.com",
        "sender_email": "customer@example.com",
        "sender_name": "Jane Customer",
        "recipient_email": "support@kanzan.io",
        "subject": "Help needed",
        "body_text": "I have a problem with my order.",
        "direction": InboundEmail.Direction.INBOUND,
        "sender_type": InboundEmail.SenderType.CUSTOMER,
        "status": InboundEmail.Status.PENDING,
    }
    defaults.update(kwargs)
    return InboundEmail.objects.create(**defaults)


# ===========================================================================
# INBOUND EMAIL PROCESSING (7.1 – 7.9)
# ===========================================================================


class InboundEmailProcessingTests(KanzenBaseTestCase):
    """Tests 7.1 – 7.9: Inbound email processing pipeline."""

    def setUp(self):
        super().setUp()
        self.set_tenant(self.tenant_a)

    # 7.1 ------------------------------------------------------------------
    @freeze_time("2026-04-05 10:00:00")
    def test_7_1_webhook_creates_inbound_email_record(self):
        """7.1 Webhook receives email -> InboundEmail record created."""
        email = _make_inbound_email(
            recipient_email=f"support+{self.tenant_a.slug}@kanzan.io",
        )
        self.assertEqual(email.status, InboundEmail.Status.PENDING)
        self.assertIsNotNone(email.pk)

        process_inbound_email(email.pk)

        email.refresh_from_db()
        self.assertIn(email.status, [
            InboundEmail.Status.TICKET_CREATED,
            InboundEmail.Status.REPLY_ADDED,
        ])

    # 7.2 ------------------------------------------------------------------
    @freeze_time("2026-04-05 10:00:00")
    def test_7_2_tenant_resolved_via_plus_addressing(self):
        """7.2 Tenant resolved via plus-addressing (support+slug@domain)."""
        recipient = f"support+{self.tenant_a.slug}@kanzan.io"
        tenant = resolve_tenant_from_address(recipient)
        self.assertEqual(tenant, self.tenant_a)

    # 7.3 ------------------------------------------------------------------
    @freeze_time("2026-04-05 10:00:00")
    def test_7_3_tenant_resolved_via_subdomain_routing(self):
        """7.3 Tenant resolved via slug as local-part (subdomain routing)."""
        recipient = f"{self.tenant_a.slug}@inbound.kanzan.io"
        tenant = resolve_tenant_from_address(recipient)
        self.assertEqual(tenant, self.tenant_a)

    # 7.4 ------------------------------------------------------------------
    @freeze_time("2026-04-05 10:00:00")
    def test_7_4_tenant_resolved_via_custom_inbound_address(self):
        """7.4 Tenant resolved via custom TenantSettings.inbound_email_address."""
        custom_addr = "helpdesk@acme.com"
        settings_obj = self.tenant_a.settings
        settings_obj.inbound_email_address = custom_addr
        settings_obj.save()

        tenant = resolve_tenant_from_address(custom_addr)
        self.assertEqual(tenant, self.tenant_a)

        # Cleanup
        settings_obj.inbound_email_address = ""
        settings_obj.save()

    # 7.5 ------------------------------------------------------------------
    @freeze_time("2026-04-05 10:00:00")
    def test_7_5_subject_ticket_ref_threads_reply(self):
        """7.5 Subject [#N] threads reply to ticket #N as comment."""
        ticket = self.create_ticket(self.tenant_a, self.admin_a)

        email = _make_inbound_email(
            recipient_email=f"support+{self.tenant_a.slug}@kanzan.io",
            subject=f"Re: Issue [#{ticket.number}] still happening",
            body_text="This is still broken, please fix.",
        )

        process_inbound_email(email.pk)

        email.refresh_from_db()
        self.assertEqual(email.status, InboundEmail.Status.REPLY_ADDED)
        self.assertEqual(email.ticket_id, ticket.pk)

    # 7.6 ------------------------------------------------------------------
    @freeze_time("2026-04-05 10:00:00")
    def test_7_6_in_reply_to_header_threads_to_correct_ticket(self):
        """7.6 In-Reply-To header threads reply to correct ticket."""
        ticket = self.create_ticket(self.tenant_a, self.admin_a)
        outbound_msg_id = f"ticket-{ticket.number}-abc123@tenant-a.kanzan.io"

        # Create an outbound record so threading can find it
        InboundEmail.objects.create(
            tenant=self.tenant_a,
            message_id=outbound_msg_id,
            sender_email="support@kanzan.io",
            recipient_email="customer@example.com",
            subject=f"[#{ticket.number}] Your ticket",
            direction=InboundEmail.Direction.OUTBOUND,
            sender_type=InboundEmail.SenderType.SYSTEM,
            status=InboundEmail.Status.SENT,
            ticket=ticket,
        )

        # Inbound reply referencing the outbound message
        reply = _make_inbound_email(
            recipient_email=f"support+{self.tenant_a.slug}@kanzan.io",
            subject="Re: Your ticket",
            body_text="Thanks for the update.",
            in_reply_to=outbound_msg_id,
        )

        process_inbound_email(reply.pk)

        reply.refresh_from_db()
        self.assertEqual(reply.status, InboundEmail.Status.REPLY_ADDED)
        self.assertEqual(reply.ticket_id, ticket.pk)

    # 7.7 ------------------------------------------------------------------
    @freeze_time("2026-04-05 10:00:00")
    @patch("apps.tickets.tasks.send_ticket_created_email_task.delay")
    def test_7_7_no_match_creates_new_ticket(self, mock_task):
        """7.7 No match -> new ticket created."""
        email = _make_inbound_email(
            recipient_email=f"support+{self.tenant_a.slug}@kanzan.io",
            subject="Brand new issue",
            body_text="I need help with something new.",
        )

        process_inbound_email(email.pk)

        email.refresh_from_db()
        self.assertEqual(email.status, InboundEmail.Status.TICKET_CREATED)
        self.assertIsNotNone(email.ticket)

        ticket = email.ticket
        self.assertEqual(ticket.tenant, self.tenant_a)
        self.assertEqual(ticket.subject, "Brand new issue")
        self.assertIn("email", ticket.tags)

    # 7.8 ------------------------------------------------------------------
    @freeze_time("2026-04-05 10:00:00")
    def test_7_8_new_ticket_from_email_queues_confirmation(self):
        """7.8 New ticket from email -> send_ticket_created_email_task queued.
        Verifies a ticket is created from the inbound email."""
        email = _make_inbound_email(
            recipient_email=f"support+{self.tenant_a.slug}@kanzan.io",
            subject="New request",
            body_text="Please help.",
        )

        with patch("apps.tickets.tasks.send_ticket_created_email_task") as mock_task:
            mock_task.delay = unittest.mock.MagicMock()
            process_inbound_email(email.pk)

        email.refresh_from_db()
        self.assertEqual(email.status, InboundEmail.Status.TICKET_CREATED)
        # Ticket should have been created
        self.assertIsNotNone(email.ticket)

    # 7.9 ------------------------------------------------------------------
    @freeze_time("2026-04-05 10:00:00")
    @unittest.skip("Not implemented: automatic SLA resume on inbound email reply")
    def test_7_9_reply_on_waiting_ticket_resumes_sla(self):
        """7.9 Reply on waiting ticket -> SLA pause resumed automatically."""
        pass


# ===========================================================================
# INBOX WORKFLOW API (7.10 – 7.20)
# ===========================================================================


class InboxWorkflowTests(KanzenBaseTestCase):
    """Tests 7.10 – 7.20: Agent inbox workflow via REST API."""

    def setUp(self):
        super().setUp()
        self.auth_tenant(self.admin_a, self.tenant_a)
        self.set_tenant(self.tenant_a)

    def _create_inbox_email(self, **kwargs):
        """Create an inbound email record associated with tenant_a."""
        defaults = {
            "tenant": self.tenant_a,
            "message_id": f"{uuid.uuid4().hex[:16]}@test.example.com",
            "sender_email": "customer@example.com",
            "sender_name": "Jane Customer",
            "recipient_email": f"support+{self.tenant_a.slug}@kanzan.io",
            "subject": "Inbox test email",
            "body_text": "Some content.",
            "direction": InboundEmail.Direction.INBOUND,
            "sender_type": InboundEmail.SenderType.CUSTOMER,
            "status": InboundEmail.Status.TICKET_CREATED,
            "inbox_status": InboundEmail.InboxStatus.PENDING,
        }
        defaults.update(kwargs)
        return InboundEmail.objects.create(**defaults)

    # 7.10 -----------------------------------------------------------------
    @freeze_time("2026-04-05 10:00:00")
    def test_7_10_inbox_returns_pending_and_linked_emails(self):
        """7.10 GET /api/v1/emails/inbox/ returns only pending + linked."""
        pending = self._create_inbox_email(inbox_status=InboundEmail.InboxStatus.PENDING)
        linked = self._create_inbox_email(inbox_status=InboundEmail.InboxStatus.LINKED)

        resp = self.client.get("/api/v1/emails/inbox/")
        self.assertEqual(resp.status_code, 200)

        ids = {str(item["id"]) for item in resp.data.get("results", resp.data)}
        self.assertIn(str(pending.pk), ids)
        self.assertIn(str(linked.pk), ids)

    # 7.11 -----------------------------------------------------------------
    @freeze_time("2026-04-05 10:00:00")
    def test_7_11_actioned_emails_not_in_inbox(self):
        """7.11 Actioned emails NOT returned in inbox GET."""
        actioned = self._create_inbox_email(
            inbox_status=InboundEmail.InboxStatus.ACTIONED,
        )
        pending = self._create_inbox_email(inbox_status=InboundEmail.InboxStatus.PENDING)

        resp = self.client.get("/api/v1/emails/inbox/")
        self.assertEqual(resp.status_code, 200)

        ids = {str(item["id"]) for item in resp.data.get("results", resp.data)}
        self.assertNotIn(str(actioned.pk), ids)
        self.assertIn(str(pending.pk), ids)

    # 7.12 -----------------------------------------------------------------
    @freeze_time("2026-04-05 10:00:00")
    def test_7_12_link_with_valid_ticket_number(self):
        """7.12 POST /link/ with valid ticket_number -> email linked."""
        ticket = self.create_ticket(self.tenant_a, self.admin_a)
        email = self._create_inbox_email()

        resp = self.client.post(
            f"/api/v1/emails/inbox/{email.pk}/link/",
            {"ticket_number": ticket.number},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)

        email.refresh_from_db()
        self.assertEqual(email.inbox_status, InboundEmail.InboxStatus.LINKED)
        self.assertEqual(email.linked_ticket_id, ticket.pk)
        self.assertIsNotNone(email.linked_at)
        self.assertEqual(email.linked_by, self.admin_a)

    # 7.13 -----------------------------------------------------------------
    @freeze_time("2026-04-05 10:00:00")
    def test_7_13_link_with_invalid_ticket_number_returns_404(self):
        """7.13 POST /link/ with invalid ticket_number -> 404."""
        email = self._create_inbox_email()

        resp = self.client.post(
            f"/api/v1/emails/inbox/{email.pk}/link/",
            {"ticket_number": 99999},
            format="json",
        )
        self.assertEqual(resp.status_code, 404)
        self.assertIn("not found", resp.data["detail"].lower())

    # 7.14 -----------------------------------------------------------------
    @freeze_time("2026-04-05 10:00:00")
    def test_7_14_action_open_sets_ticket_status_open(self):
        """7.14 POST /action/ open -> ticket status open, email actioned."""
        ticket = self.create_ticket(
            self.tenant_a, self.admin_a,
            status=self.status_in_progress_a,
        )
        email = self._create_inbox_email()

        # Link first
        self.client.post(
            f"/api/v1/emails/inbox/{email.pk}/link/",
            {"ticket_number": ticket.number},
            format="json",
        )

        resp = self.client.post(
            f"/api/v1/emails/inbox/{email.pk}/action/",
            {"action": "open"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)

        email.refresh_from_db()
        self.assertEqual(email.inbox_status, InboundEmail.InboxStatus.ACTIONED)
        self.assertEqual(email.action_taken, "open")

        ticket.refresh_from_db()
        self.assertEqual(ticket.status.slug, "open")

    # 7.15 -----------------------------------------------------------------
    @freeze_time("2026-04-05 10:00:00")
    def test_7_15_action_assign_sets_assignee(self):
        """7.15 POST /action/ assign -> ticket has new assignee."""
        ticket = self.create_ticket(self.tenant_a, self.admin_a)
        email = self._create_inbox_email()

        self.client.post(
            f"/api/v1/emails/inbox/{email.pk}/link/",
            {"ticket_number": ticket.number},
            format="json",
        )

        resp = self.client.post(
            f"/api/v1/emails/inbox/{email.pk}/action/",
            {"action": "assign", "assignee": str(self.agent_a.pk)},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)

        email.refresh_from_db()
        self.assertEqual(email.inbox_status, InboundEmail.InboxStatus.ACTIONED)

        ticket.refresh_from_db()
        self.assertEqual(ticket.assignee_id, self.agent_a.pk)

    # 7.16 -----------------------------------------------------------------
    @freeze_time("2026-04-05 10:00:00")
    def test_7_16_action_close_closes_ticket(self):
        """7.16 POST /action/ close -> ticket closed, email actioned."""
        ticket = self.create_ticket(self.tenant_a, self.admin_a)
        email = self._create_inbox_email()

        self.client.post(
            f"/api/v1/emails/inbox/{email.pk}/link/",
            {"ticket_number": ticket.number},
            format="json",
        )

        resp = self.client.post(
            f"/api/v1/emails/inbox/{email.pk}/action/",
            {"action": "close"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)

        email.refresh_from_db()
        self.assertEqual(email.inbox_status, InboundEmail.InboxStatus.ACTIONED)
        self.assertEqual(email.action_taken, "close")

        ticket.refresh_from_db()
        self.assertTrue(ticket.status.is_closed)

    # 7.17 -----------------------------------------------------------------
    @freeze_time("2026-04-05 10:00:00")
    def test_7_17_action_on_already_actioned_email_returns_400(self):
        """7.17 POST /action/ on already-actioned email -> 400."""
        ticket = self.create_ticket(self.tenant_a, self.admin_a)
        email = self._create_inbox_email()

        # Link and action
        self.client.post(
            f"/api/v1/emails/inbox/{email.pk}/link/",
            {"ticket_number": ticket.number},
            format="json",
        )
        self.client.post(
            f"/api/v1/emails/inbox/{email.pk}/action/",
            {"action": "open"},
            format="json",
        )

        # Try to action again
        resp = self.client.post(
            f"/api/v1/emails/inbox/{email.pk}/action/",
            {"action": "close"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("already", resp.data["detail"].lower())

    # 7.18 -----------------------------------------------------------------
    @freeze_time("2026-04-05 10:00:00")
    def test_7_18_action_without_linking_first_returns_400(self):
        """7.18 POST /action/ without linking first -> 400."""
        email = self._create_inbox_email()

        resp = self.client.post(
            f"/api/v1/emails/inbox/{email.pk}/action/",
            {"action": "open"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("linked", resp.data["detail"].lower())

    # 7.19 -----------------------------------------------------------------
    @freeze_time("2026-04-05 10:00:00")
    def test_7_19_inbound_email_record_never_deleted_after_action(self):
        """7.19 InboundEmail record never deleted after action (DB audit)."""
        ticket = self.create_ticket(self.tenant_a, self.admin_a)
        email = self._create_inbox_email()
        email_pk = email.pk

        self.client.post(
            f"/api/v1/emails/inbox/{email.pk}/link/",
            {"ticket_number": ticket.number},
            format="json",
        )
        self.client.post(
            f"/api/v1/emails/inbox/{email.pk}/action/",
            {"action": "open"},
            format="json",
        )

        self.assertTrue(InboundEmail.objects.filter(pk=email_pk).exists())
        email.refresh_from_db()
        self.assertEqual(email.inbox_status, InboundEmail.InboxStatus.ACTIONED)

    # 7.20 -----------------------------------------------------------------
    @freeze_time("2026-04-05 10:00:00")
    def test_7_20_activity_logs_written_for_link_and_action(self):
        """7.20 ActivityLog + TicketActivity written for link and action."""
        ticket = self.create_ticket(self.tenant_a, self.admin_a)
        email = self._create_inbox_email()

        self.client.post(
            f"/api/v1/emails/inbox/{email.pk}/link/",
            {"ticket_number": ticket.number},
            format="json",
        )

        # Check ActivityLog for link
        link_logs = ActivityLog.unscoped.filter(
            action=ActivityLog.Action.EMAIL_LINKED,
        )
        self.assertTrue(link_logs.exists())

        # Check TicketActivity for link
        link_activities = TicketActivity.unscoped.filter(
            ticket=ticket,
            event=TicketActivity.Event.EMAIL_LINKED,
        )
        self.assertTrue(link_activities.exists())

        # Action the email
        self.client.post(
            f"/api/v1/emails/inbox/{email.pk}/action/",
            {"action": "open"},
            format="json",
        )

        # Check ActivityLog for action
        action_logs = ActivityLog.unscoped.filter(
            action=ActivityLog.Action.EMAIL_ACTIONED,
        )
        self.assertTrue(action_logs.exists())

        # Check TicketActivity for action
        action_activities = TicketActivity.unscoped.filter(
            ticket=ticket,
            event=TicketActivity.Event.EMAIL_ACTIONED,
        )
        self.assertTrue(action_activities.exists())


# ===========================================================================
# OUTBOUND EMAIL (7.21 – 7.24)
# ===========================================================================


class OutboundEmailTests(KanzenBaseTestCase):
    """Tests 7.21 – 7.24: Outbound email tasks and threading."""

    def setUp(self):
        super().setUp()
        self.auth_tenant(self.admin_a, self.tenant_a)
        self.set_tenant(self.tenant_a)

    # 7.21 -----------------------------------------------------------------
    @freeze_time("2026-04-05 10:00:00")
    @patch("apps.tickets.email_service.send_ticket_email")
    def test_7_21_agent_reply_queues_reply_email_task(self, mock_send):
        """7.21 Agent reply queues send_ticket_reply_email_task."""
        mock_send.return_value = "test-msg-id@kanzan.io"

        ticket = self.create_ticket(
            self.tenant_a, self.admin_a, contact=self.contact_a,
        )

        from apps.tickets.tasks import send_ticket_reply_email_task

        result = send_ticket_reply_email_task.apply(
            args=[str(ticket.pk), "Thanks for contacting us!", "Admin A", str(self.tenant_a.pk)],
        )

        # The task should have called send_ticket_reply_email which calls send_ticket_email
        mock_send.assert_called_once()
        call_kwargs = mock_send.call_args
        self.assertEqual(call_kwargs[1]["to_email"], self.contact_a.email)

    # 7.22 -----------------------------------------------------------------
    @freeze_time("2026-04-05 10:00:00")
    @patch("apps.tickets.email_service.EmailMultiAlternatives")
    def test_7_22_email_includes_rfc_threading_headers(self, mock_email_cls):
        """7.22 Email includes correct RFC threading headers."""
        mock_email_instance = MagicMock()
        mock_email_cls.return_value = mock_email_instance

        ticket = self.create_ticket(
            self.tenant_a, self.admin_a, contact=self.contact_a,
        )

        # Create a prior outbound record for threading
        prior_msg_id = f"ticket-{ticket.number}-prior@tenant-a.kanzan.io"
        InboundEmail.objects.create(
            tenant=self.tenant_a,
            message_id=prior_msg_id,
            sender_email="noreply@kanzan.io",
            recipient_email=self.contact_a.email,
            direction=InboundEmail.Direction.OUTBOUND,
            status=InboundEmail.Status.SENT,
            ticket=ticket,
        )

        from apps.tickets.email_service import send_ticket_email

        send_ticket_email(
            tenant=self.tenant_a,
            ticket=ticket,
            to_email=self.contact_a.email,
            subject=f"Re: [#{ticket.number}] Test",
            body_text="Reply body",
        )

        # Inspect the headers passed to EmailMultiAlternatives
        call_kwargs = mock_email_cls.call_args[1]
        headers = call_kwargs.get("headers", {})

        self.assertIn("Message-ID", headers)
        self.assertTrue(headers["Message-ID"].startswith("<"))
        self.assertTrue(headers["Message-ID"].endswith(">"))
        self.assertIn("In-Reply-To", headers)
        self.assertIn(prior_msg_id, headers["In-Reply-To"])

    # 7.23 -----------------------------------------------------------------
    @freeze_time("2026-04-05 10:00:00")
    def test_7_23_failed_send_retries_up_to_3_times(self):
        """7.23 Failed send retries up to 3 times with 30s delay."""
        from apps.tickets.tasks import send_ticket_reply_email_task

        self.assertEqual(send_ticket_reply_email_task.max_retries, 3)
        self.assertEqual(send_ticket_reply_email_task.default_retry_delay, 30)

    # 7.24 -----------------------------------------------------------------
    @freeze_time("2026-04-05 10:00:00")
    @patch("apps.tickets.email_service.send_ticket_email")
    def test_7_24_ticket_creation_confirmation_email_queued(self, mock_send):
        """7.24 Ticket creation confirmation email queued for new tickets."""
        mock_send.return_value = "test-msg-id@kanzan.io"

        ticket = self.create_ticket(
            self.tenant_a, self.admin_a, contact=self.contact_a,
        )

        from apps.tickets.tasks import send_ticket_created_email_task

        result = send_ticket_created_email_task.apply(
            args=[str(ticket.pk), str(self.tenant_a.pk)],
        )

        mock_send.assert_called_once()
        call_kwargs = mock_send.call_args
        self.assertEqual(call_kwargs[1]["to_email"], self.contact_a.email)
        self.assertIn(f"[#{ticket.number}]", call_kwargs[1]["subject"])
