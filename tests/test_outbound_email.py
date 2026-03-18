"""
Tests for outbound email notifications to contacts.

Covers: reply emails, ticket created emails, Reply-To threading,
message-id recording for inbound threading, and signal integration.
"""

from unittest.mock import patch

from django.core import mail
from django.test import override_settings

from apps.contacts.models import Contact
from apps.inbound_email.models import InboundEmail
from apps.tickets.email_service import (
    send_ticket_created_email,
    send_ticket_reply_email,
    _get_reply_to_address,
    _get_ticket_url,
)

from tests.base import TenantTestCase


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="noreply@kanzan.test",
    BASE_DOMAIN="kanzan.test",
    BASE_PORT="8001",
    DEBUG=True,
)
class TicketReplyEmailTest(TenantTestCase):
    """Test outbound reply email to contact."""

    def setUp(self):
        super().setUp()
        self.set_tenant(self.tenant_a)
        self.contact = Contact.objects.create(
            email="customer@example.com",
            first_name="Jane",
            last_name="Doe",
        )
        self.ticket = self.make_ticket(
            self.tenant_a,
            self.admin_a,
            subject="Login issue",
            contact=self.contact,
        )

    def test_sends_reply_email(self):
        """Reply email is sent to the contact."""
        result = send_ticket_reply_email(
            self.ticket, "We've reset your password.", "Admin A", self.tenant_a,
        )
        self.assertTrue(result)
        self.assertEqual(len(mail.outbox), 1)

        email = mail.outbox[0]
        self.assertEqual(email.to, ["customer@example.com"])
        self.assertIn("[#1]", email.subject)
        self.assertIn("Login issue", email.subject)
        self.assertIn("Re:", email.subject)

    def test_reply_to_header_set(self):
        """Reply-To points to tenant inbound address for threading."""
        send_ticket_reply_email(
            self.ticket, "Test reply.", "Admin A", self.tenant_a,
        )
        email = mail.outbox[0]
        self.assertEqual(
            email.reply_to,
            [f"support+{self.tenant_a.slug}@kanzan.test"],
        )

    def test_custom_inbound_address_as_reply_to(self):
        """If tenant has custom inbound address, use it as Reply-To."""
        self.tenant_a.settings.inbound_email_address = "help@acme.com"
        self.tenant_a.settings.save(update_fields=["inbound_email_address"])

        send_ticket_reply_email(
            self.ticket, "Test reply.", "Admin A", self.tenant_a,
        )
        email = mail.outbox[0]
        self.assertEqual(email.reply_to, ["help@acme.com"])

    def test_message_id_recorded_for_threading(self):
        """Outbound message_id is stored so inbound replies can thread."""
        send_ticket_reply_email(
            self.ticket, "Test reply.", "Admin A", self.tenant_a,
        )
        # Should create an InboundEmail record with the outbound message_id
        records = InboundEmail.objects.filter(
            ticket=self.ticket,
            sender_email="noreply@kanzan.test",
        )
        self.assertEqual(records.count(), 1)
        self.assertEqual(records.first().status, InboundEmail.Status.REPLY_ADDED)

    def test_skips_if_no_contact(self):
        """No email sent if ticket has no contact."""
        self.ticket.contact = None
        self.ticket.save(update_fields=["contact"])
        result = send_ticket_reply_email(
            self.ticket, "Test.", "Admin A", self.tenant_a,
        )
        self.assertFalse(result)
        self.assertEqual(len(mail.outbox), 0)

    def test_skips_if_no_contact_email(self):
        """No email sent if contact has no email."""
        self.contact.email = ""
        self.contact.save(update_fields=["email"])
        self.ticket.refresh_from_db()
        result = send_ticket_reply_email(
            self.ticket, "Test.", "Admin A", self.tenant_a,
        )
        self.assertFalse(result)

    def test_html_alternative_included(self):
        """Email includes HTML alternative."""
        send_ticket_reply_email(
            self.ticket, "HTML test.", "Admin A", self.tenant_a,
        )
        email = mail.outbox[0]
        self.assertEqual(len(email.alternatives), 1)
        html_content, mime_type = email.alternatives[0]
        self.assertEqual(mime_type, "text/html")
        self.assertIn("[#1]", html_content)
        self.assertIn("Login issue", html_content)

    def test_from_address_includes_tenant_name(self):
        """From address uses tenant name as display name."""
        send_ticket_reply_email(
            self.ticket, "Test.", "Admin A", self.tenant_a,
        )
        email = mail.outbox[0]
        self.assertIn(self.tenant_a.name, email.from_email)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="noreply@kanzan.test",
    BASE_DOMAIN="kanzan.test",
    BASE_PORT="8001",
    DEBUG=True,
)
class TicketCreatedEmailTest(TenantTestCase):
    """Test confirmation email when ticket is created."""

    def setUp(self):
        super().setUp()
        self.set_tenant(self.tenant_a)
        self.contact = Contact.objects.create(
            email="newcustomer@example.com",
            first_name="New",
            last_name="Customer",
        )
        self.ticket = self.make_ticket(
            self.tenant_a,
            self.admin_a,
            subject="Password reset",
            contact=self.contact,
        )

    def test_sends_created_email(self):
        """Confirmation email sent to contact on ticket creation."""
        result = send_ticket_created_email(self.ticket, self.tenant_a)
        self.assertTrue(result)
        self.assertEqual(len(mail.outbox), 1)

        email = mail.outbox[0]
        self.assertEqual(email.to, ["newcustomer@example.com"])
        self.assertIn("[#", email.subject)
        self.assertIn("Password reset", email.subject)
        self.assertNotIn("Re:", email.subject)

    def test_reply_to_set_for_threading(self):
        """Reply-To on created email enables customer replies."""
        send_ticket_created_email(self.ticket, self.tenant_a)
        email = mail.outbox[0]
        self.assertTrue(len(email.reply_to) > 0)


class ReplyToAddressTest(TenantTestCase):
    """Test reply-to address resolution."""

    @override_settings(BASE_DOMAIN="kanzan.test")
    def test_default_plus_addressing(self):
        """Without custom address, uses support+slug@domain."""
        address = _get_reply_to_address(self.tenant_a)
        self.assertEqual(address, f"support+{self.tenant_a.slug}@kanzan.test")

    def test_custom_inbound_address(self):
        """Custom inbound_email_address takes priority."""
        self.tenant_a.settings.inbound_email_address = "support@acme.com"
        self.tenant_a.settings.save(update_fields=["inbound_email_address"])
        address = _get_reply_to_address(self.tenant_a)
        self.assertEqual(address, "support@acme.com")


@override_settings(
    BASE_DOMAIN="kanzan.test",
    BASE_PORT="8001",
    DEBUG=True,
)
class TicketUrlTest(TenantTestCase):
    """Test ticket URL generation."""

    def test_subdomain_url(self):
        ticket = self.make_ticket(self.tenant_a, self.admin_a)
        url = _get_ticket_url(self.tenant_a, ticket)
        self.assertIn(self.tenant_a.slug, url)
        self.assertIn(f"/tickets/{ticket.number}/", url)

    def test_custom_domain_url(self):
        self.tenant_a.domain = "crm.acme.com"
        self.tenant_a.save(update_fields=["domain"])
        ticket = self.make_ticket(self.tenant_a, self.admin_a)
        url = _get_ticket_url(self.tenant_a, ticket)
        self.assertIn("crm.acme.com", url)
