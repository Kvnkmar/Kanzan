"""
Phase 4g — Inbound email tests.

Covers:
- Tenant resolution from email address
- Ticket number extraction from subject
- Quote stripping
- Full email processing pipeline
- SendGrid webhook endpoint
"""

import pytest

from conftest import (
    InboundEmailFactory,
    MembershipFactory,
    RoleFactory,
    TenantFactory,
    TicketFactory,
    TicketStatusFactory,
    UserFactory,
)
from main.context import clear_current_tenant, set_current_tenant


@pytest.mark.django_db
class TestTenantResolution:
    def test_plus_addressing(self):
        from apps.inbound_email.services import resolve_tenant_from_address
        tenant = TenantFactory(slug="acme")
        result = resolve_tenant_from_address("support+acme@kanzan.io")
        assert result == tenant

    def test_slug_local_part(self):
        from apps.inbound_email.services import resolve_tenant_from_address
        tenant = TenantFactory(slug="demo")
        result = resolve_tenant_from_address("demo@inbound.kanzan.io")
        assert result == tenant

    def test_unknown_address_returns_none(self):
        from apps.inbound_email.services import resolve_tenant_from_address
        result = resolve_tenant_from_address("nobody@nowhere.com")
        assert result is None


@pytest.mark.django_db
class TestTicketNumberExtraction:
    def test_extracts_number(self):
        from apps.inbound_email.services import extract_ticket_number
        assert extract_ticket_number("Re: [#42] Issue with login") == 42

    def test_extracts_with_ticket_prefix(self):
        from apps.inbound_email.services import extract_ticket_number
        assert extract_ticket_number("Re: [Ticket #99] Bug") == 99

    def test_no_number_returns_none(self):
        from apps.inbound_email.services import extract_ticket_number
        assert extract_ticket_number("Just a normal subject") is None


class TestQuoteStripping:
    def test_strips_quoted_lines(self):
        from apps.inbound_email.services import strip_quoted_reply
        body = "Hello\n> Previous message\nThanks"
        result = strip_quoted_reply(body)
        assert "> Previous message" not in result
        assert "Hello" in result

    def test_strips_on_wrote(self):
        from apps.inbound_email.services import strip_quoted_reply
        body = "My reply\n\nOn Monday, Jan 1 wrote:\n> old stuff"
        result = strip_quoted_reply(body)
        assert "My reply" in result
        assert "old stuff" not in result

    def test_handles_empty(self):
        from apps.inbound_email.services import strip_quoted_reply
        assert strip_quoted_reply("") == ""
        assert strip_quoted_reply(None) == ""


@pytest.mark.django_db
class TestProcessInboundEmail:
    def test_creates_ticket_from_new_email(self):
        tenant = TenantFactory(slug="inbound-test")
        user = UserFactory()

        from apps.accounts.models import Role
        admin_role = Role.unscoped.get(tenant=tenant, slug="admin")
        MembershipFactory(user=user, tenant=tenant, role=admin_role)

        set_current_tenant(tenant)
        TicketStatusFactory(tenant=tenant, is_default=True, name="Open", slug="open")
        clear_current_tenant()

        inbound = InboundEmailFactory(
            recipient_email="support+inbound-test@kanzan.io",
            tenant=tenant,
            subject="Help me please",
            body_text="I need help with my account",
        )

        from apps.inbound_email.services import process_inbound_email
        process_inbound_email(inbound.pk)

        inbound.refresh_from_db()
        assert inbound.status == "ticket_created"
        assert inbound.ticket is not None

    def test_rejects_unknown_tenant(self):
        inbound = InboundEmailFactory(
            recipient_email="nobody@unknown.com",
            tenant=None,
        )

        from apps.inbound_email.services import process_inbound_email
        process_inbound_email(inbound.pk)

        inbound.refresh_from_db()
        assert inbound.status == "rejected"


@pytest.mark.django_db
class TestSendGridWebhook:
    def test_rejects_without_secret(self, client):
        resp = client.post(
            "/inbound/email/sendgrid/",
            data={"from": "test@example.com", "subject": "Test"},
        )
        assert resp.status_code == 403

    def test_accepts_with_valid_secret(self, client, settings):
        settings.INBOUND_EMAIL_WEBHOOK_SECRET = "testsecret123"
        resp = client.post(
            "/inbound/email/sendgrid/?secret=testsecret123",
            data={
                "from": "Test User <test@example.com>",
                "to": "support+demo@kanzan.io",
                "subject": "Test email",
                "text": "Hello world",
                "headers": "",
            },
        )
        assert resp.status_code == 200
