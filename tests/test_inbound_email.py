"""
Tests for the inbound email-to-ticket system.

Covers: tenant resolution, ticket creation, reply threading,
contact auto-creation, dedup, quoted reply stripping, and
webhook endpoint authentication.
"""

import time
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.test.client import RequestFactory

from apps.comments.models import Comment
from apps.contacts.models import Contact
from apps.inbound_email.models import InboundEmail
from apps.inbound_email.services import (
    extract_ticket_number,
    find_or_create_contact,
    process_inbound_email,
    resolve_tenant_from_address,
    strip_quoted_reply,
)
from apps.tickets.models import Ticket

from tests.base import TenantTestCase


class TenantResolutionTest(TenantTestCase):
    """Test tenant resolution from recipient email address."""

    def test_plus_addressing(self):
        """support+tenant-a@kanzan.io resolves to tenant_a."""
        tenant = resolve_tenant_from_address("support+tenant-a@kanzan.io")
        self.assertEqual(tenant, self.tenant_a)

    def test_slug_as_local_part(self):
        """tenant-a@inbound.kanzan.io resolves to tenant_a."""
        tenant = resolve_tenant_from_address("tenant-a@inbound.kanzan.io")
        self.assertEqual(tenant, self.tenant_a)

    def test_custom_inbound_address(self):
        """Custom inbound_email_address on TenantSettings works."""
        self.tenant_a.settings.inbound_email_address = "help@acme.com"
        self.tenant_a.settings.save(update_fields=["inbound_email_address"])

        tenant = resolve_tenant_from_address("help@acme.com")
        self.assertEqual(tenant, self.tenant_a)

    def test_unknown_address_returns_none(self):
        tenant = resolve_tenant_from_address("unknown@nowhere.com")
        self.assertIsNone(tenant)

    def test_inactive_tenant_not_resolved(self):
        """Inactive tenants are not resolved."""
        self.tenant_a.is_active = False
        self.tenant_a.save(update_fields=["is_active"])
        tenant = resolve_tenant_from_address("support+tenant-a@kanzan.io")
        self.assertIsNone(tenant)
        # Restore
        self.tenant_a.is_active = True
        self.tenant_a.save(update_fields=["is_active"])


class TicketNumberExtractionTest(TestCase):
    """Test subject line ticket number parsing."""

    def test_standard_format(self):
        self.assertEqual(extract_ticket_number("Re: [#42] Password reset"), 42)

    def test_ticket_prefix(self):
        self.assertEqual(extract_ticket_number("Re: [Ticket #123] Help"), 123)

    def test_no_ticket_number(self):
        self.assertIsNone(extract_ticket_number("Just a regular email"))

    def test_case_insensitive(self):
        self.assertEqual(extract_ticket_number("[ticket #7] test"), 7)


class QuotedReplyStrippingTest(TestCase):
    """Test stripping of quoted replies from email bodies."""

    def test_strip_angle_bracket_quotes(self):
        body = "This is my reply.\n\n> Original message here.\n> More original."
        result = strip_quoted_reply(body)
        self.assertEqual(result, "This is my reply.")

    def test_strip_on_wrote_block(self):
        body = "Thanks for the update.\n\nOn Mon, Jan 1, 2026, John wrote:\nOld text"
        result = strip_quoted_reply(body)
        self.assertEqual(result, "Thanks for the update.")

    def test_strip_original_message_separator(self):
        body = "My reply here.\n\n--- Original Message ---\nOld stuff"
        result = strip_quoted_reply(body)
        self.assertEqual(result, "My reply here.")

    def test_empty_body(self):
        self.assertEqual(strip_quoted_reply(""), "")

    def test_no_quotes_unchanged(self):
        body = "Just a plain message with no quotes."
        self.assertEqual(strip_quoted_reply(body), body)


class ContactAutoCreationTest(TenantTestCase):
    """Test auto-creation and lookup of contacts from sender email."""

    def test_creates_new_contact(self):
        self.set_tenant(self.tenant_a)
        contact, created = find_or_create_contact(
            self.tenant_a, "new@customer.com", "Jane Doe",
        )
        self.assertTrue(created)
        self.assertEqual(contact.email, "new@customer.com")
        self.assertEqual(contact.first_name, "Jane")
        self.assertEqual(contact.last_name, "Doe")
        self.assertEqual(contact.tenant, self.tenant_a)

    def test_finds_existing_contact(self):
        self.set_tenant(self.tenant_a)
        Contact.objects.create(
            email="existing@customer.com",
            first_name="Existing",
            last_name="Customer",
        )
        contact, created = find_or_create_contact(
            self.tenant_a, "existing@customer.com",
        )
        self.assertFalse(created)
        self.assertEqual(contact.first_name, "Existing")

    def test_name_from_email_when_no_sender_name(self):
        self.set_tenant(self.tenant_a)
        contact, created = find_or_create_contact(
            self.tenant_a, "john.smith@example.com",
        )
        self.assertTrue(created)
        self.assertEqual(contact.first_name, "john.smith")


class EmailToTicketCreationTest(TenantTestCase):
    """Test full email-to-ticket creation flow."""

    def _create_inbound(self, **kwargs):
        defaults = {
            "message_id": f"test-{time.time()}@example.com",
            "sender_email": "customer@example.com",
            "sender_name": "Test Customer",
            "recipient_email": f"support+{self.tenant_a.slug}@kanzan.io",
            "subject": "I need help with my account",
            "body_text": "Please help me reset my password.",
        }
        defaults.update(kwargs)
        return InboundEmail.objects.create(**defaults)

    def test_creates_ticket_from_new_email(self):
        """A new inbound email creates a ticket."""
        inbound = self._create_inbound()
        process_inbound_email(inbound.pk)

        inbound.refresh_from_db()
        self.assertEqual(inbound.status, InboundEmail.Status.TICKET_CREATED)
        self.assertIsNotNone(inbound.ticket)
        self.assertEqual(inbound.ticket.subject, "I need help with my account")
        self.assertEqual(inbound.ticket.tenant, self.tenant_a)
        self.assertEqual(inbound.ticket.tags, ["email"])

    def test_auto_creates_contact(self):
        """A new inbound email auto-creates a contact if not found."""
        inbound = self._create_inbound(
            sender_email="brand-new@customer.com",
            sender_name="Brand New",
        )
        process_inbound_email(inbound.pk)

        self.set_tenant(self.tenant_a)
        contact = Contact.objects.get(email="brand-new@customer.com")
        self.assertEqual(contact.first_name, "Brand")
        self.assertEqual(contact.last_name, "New")

    def test_links_to_existing_contact(self):
        """If contact exists, ticket is linked to it."""
        self.set_tenant(self.tenant_a)
        existing = Contact.objects.create(
            email="known@customer.com",
            first_name="Known",
            last_name="Customer",
        )

        inbound = self._create_inbound(sender_email="known@customer.com")
        process_inbound_email(inbound.pk)

        inbound.refresh_from_db()
        self.assertEqual(inbound.ticket.contact, existing)

    def test_rejects_unknown_tenant(self):
        """Email to unknown tenant is rejected."""
        inbound = self._create_inbound(
            recipient_email="support+nonexistent@kanzan.io",
        )
        process_inbound_email(inbound.pk)

        inbound.refresh_from_db()
        self.assertEqual(inbound.status, InboundEmail.Status.REJECTED)
        self.assertIn("tenant", inbound.error_message.lower())

    def test_dedup_prevents_double_processing(self):
        """Same message_id is not processed twice."""
        msg_id = "unique-msg@example.com"
        inbound1 = self._create_inbound(message_id=msg_id)
        process_inbound_email(inbound1.pk)

        inbound2 = self._create_inbound(message_id=msg_id)
        process_inbound_email(inbound2.pk)

        inbound2.refresh_from_db()
        self.assertEqual(inbound2.status, InboundEmail.Status.REJECTED)
        self.assertIn("duplicate", inbound2.error_message.lower())


class EmailReplyThreadingTest(TenantTestCase):
    """Test threading of email replies to existing tickets."""

    def test_reply_via_subject_ticket_number(self):
        """Reply with [#N] in subject threads to existing ticket."""
        ticket = self.make_ticket(
            self.tenant_a, self.admin_a, subject="Original issue",
        )
        inbound = InboundEmail.objects.create(
            message_id="reply-1@example.com",
            sender_email="customer@example.com",
            recipient_email=f"support+{self.tenant_a.slug}@kanzan.io",
            subject=f"Re: [#{ticket.number}] Original issue",
            body_text="Here is my follow-up.",
        )
        process_inbound_email(inbound.pk)

        inbound.refresh_from_db()
        self.assertEqual(inbound.status, InboundEmail.Status.REPLY_ADDED)
        self.assertEqual(inbound.ticket, ticket)

    def test_reply_via_in_reply_to_header(self):
        """Reply matched via In-Reply-To header."""
        # First email creates a ticket
        first = InboundEmail.objects.create(
            message_id="original@example.com",
            sender_email="customer@example.com",
            recipient_email=f"support+{self.tenant_a.slug}@kanzan.io",
            subject="Need help",
            body_text="Original message.",
        )
        process_inbound_email(first.pk)
        first.refresh_from_db()
        ticket = first.ticket

        # Reply references the original message_id
        reply = InboundEmail.objects.create(
            message_id="reply@example.com",
            in_reply_to="original@example.com",
            sender_email="customer@example.com",
            recipient_email=f"support+{self.tenant_a.slug}@kanzan.io",
            subject="Re: Need help",
            body_text="Follow-up info.",
        )
        process_inbound_email(reply.pk)

        reply.refresh_from_db()
        self.assertEqual(reply.status, InboundEmail.Status.REPLY_ADDED)
        self.assertEqual(reply.ticket, ticket)

    def test_reply_creates_comment(self):
        """A threaded reply adds a Comment to the ticket."""
        ticket = self.make_ticket(
            self.tenant_a, self.admin_a, subject="Test ticket",
        )
        inbound = InboundEmail.objects.create(
            message_id="comment-reply@example.com",
            sender_email="customer@example.com",
            recipient_email=f"support+{self.tenant_a.slug}@kanzan.io",
            subject=f"Re: [#{ticket.number}] Test ticket",
            body_text="Adding more details here.",
        )
        process_inbound_email(inbound.pk)

        from django.contrib.contenttypes.models import ContentType

        ct = ContentType.objects.get_for_model(Ticket)
        self.set_tenant(self.tenant_a)
        comments = Comment.objects.filter(
            content_type=ct, object_id=ticket.pk,
        )
        self.assertEqual(comments.count(), 1)
        self.assertIn("Adding more details", comments.first().body)

    def test_empty_reply_is_rejected(self):
        """A reply that is only quoted text (empty after stripping) is rejected."""
        ticket = self.make_ticket(
            self.tenant_a, self.admin_a, subject="Test ticket",
        )
        inbound = InboundEmail.objects.create(
            message_id="empty-reply@example.com",
            sender_email="customer@example.com",
            recipient_email=f"support+{self.tenant_a.slug}@kanzan.io",
            subject=f"Re: [#{ticket.number}] Test ticket",
            body_text="> Just quoted text\n> Nothing new",
        )
        process_inbound_email(inbound.pk)

        inbound.refresh_from_db()
        self.assertEqual(inbound.status, InboundEmail.Status.REJECTED)


@override_settings(INBOUND_EMAIL_WEBHOOK_SECRET="test-secret-123")
class WebhookEndpointTest(TenantTestCase):
    """Test webhook endpoint authentication and payload handling."""

    def test_sendgrid_rejects_missing_secret(self):
        """SendGrid webhook without secret returns 403."""
        response = self.client.post(
            "/inbound/email/sendgrid/",
            data={"from": "test@example.com", "subject": "Test"},
        )
        self.assertEqual(response.status_code, 403)

    @patch("apps.inbound_email.views._queue_processing")
    def test_sendgrid_accepts_valid_secret(self, mock_queue):
        """SendGrid webhook with valid secret creates InboundEmail."""
        response = self.client.post(
            "/inbound/email/sendgrid/?secret=test-secret-123",
            data={
                "from": "John Doe <john@example.com>",
                "to": "support+tenant-a@kanzan.io",
                "subject": "Help needed",
                "text": "Please help.",
                "headers": "Message-ID: <abc123@example.com>\n",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(InboundEmail.objects.filter(
            sender_email="john@example.com",
        ).exists())
        mock_queue.assert_called_once()

    def test_mailgun_rejects_missing_secret(self):
        """Mailgun webhook without secret returns 403."""
        response = self.client.post(
            "/inbound/email/mailgun/",
            data={"from": "test@example.com", "subject": "Test"},
        )
        self.assertEqual(response.status_code, 403)

    @patch("apps.inbound_email.views._queue_processing")
    def test_mailgun_accepts_valid_secret(self, mock_queue):
        """Mailgun webhook with valid secret creates InboundEmail."""
        response = self.client.post(
            "/inbound/email/mailgun/?secret=test-secret-123",
            data={
                "from": "Jane Smith <jane@example.com>",
                "recipient": "support+tenant-a@kanzan.io",
                "subject": "Mailgun test",
                "body-plain": "Test body.",
                "Message-Id": "<mg-123@example.com>",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(InboundEmail.objects.filter(
            sender_email="jane@example.com",
        ).exists())
        mock_queue.assert_called_once()
