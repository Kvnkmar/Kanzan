"""
Phase 4g — Inbound email tests.

Covers:
- Tenant resolution from email address
- Ticket number extraction from subject
- Quote stripping
- Full email processing pipeline
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
class TestEmailsInternalPersonalScope:
    """
    The personal Emails page requests ``?internal=true&mine=true``.

    It shows real mail addressed to the requesting user — agent-to-agent
    messages — but NOT auto-generated system notification emails
    (assignment/escalation alerts), which duplicate the in-app bell and
    aren't actionable. Customer mail also never appears via this scope; the
    original assigned message arrives via ``?assigned=me`` instead. Other
    consumers that omit the params (audit log, agent inbox) keep seeing
    everything.
    """

    def _make(self, tenant, **kw):
        from apps.inbound_email.models import InboundEmail
        defaults = dict(tenant=tenant, status=InboundEmail.Status.SENT)
        defaults.update(kw)
        return InboundEmailFactory(**defaults)

    def test_internal_and_mine_scopes_to_user(self, admin_user, admin_client, tenant):
        from apps.inbound_email.models import InboundEmail

        # Customer email (arrives via ?assigned=me, not here) — excluded.
        self._make(
            tenant,
            sender_type=InboundEmail.SenderType.CUSTOMER,
            direction=InboundEmail.Direction.INBOUND,
            status=InboundEmail.Status.PARKED_IN_HUB,
            recipient_email="support@kanzan.io",
            subject="Customer question",
        )
        # Auto-generated system notification addressed to me — excluded now
        # (bell-covered noise, not actionable in the inbox).
        self._make(
            tenant,
            sender_type=InboundEmail.SenderType.SYSTEM,
            direction=InboundEmail.Direction.OUTBOUND,
            recipient_email=admin_user.email,
            subject="Email assigned to you",
        )
        # Real agent-to-agent message addressed to me — included.
        agent_msg = self._make(
            tenant,
            sender_type=InboundEmail.SenderType.AGENT,
            direction=InboundEmail.Direction.OUTBOUND,
            recipient_email=admin_user.email,
            subject="A teammate pinged you",
        )
        # Agent message addressed to a different user — excluded.
        self._make(
            tenant,
            sender_type=InboundEmail.SenderType.AGENT,
            direction=InboundEmail.Direction.OUTBOUND,
            recipient_email="someone-else@test.com",
            subject="Not for me",
        )

        resp = admin_client.get("/api/v1/inbound-email/?internal=true&mine=true")
        assert resp.status_code == 200
        results = resp.json().get("results", resp.json())
        ids = {row["id"] for row in results}
        assert ids == {str(agent_msg.id)}

    def test_internal_excludes_customer_mail(self, admin_user, admin_client, tenant):
        from apps.inbound_email.models import InboundEmail

        self._make(
            tenant,
            sender_type=InboundEmail.SenderType.CUSTOMER,
            direction=InboundEmail.Direction.INBOUND,
            recipient_email=admin_user.email,  # even if addressed to me
            subject="Customer reply",
        )
        agent_send = self._make(
            tenant,
            sender_type=InboundEmail.SenderType.AGENT,
            direction=InboundEmail.Direction.OUTBOUND,
            recipient_email=admin_user.email,
            subject="Internal note",
        )

        resp = admin_client.get("/api/v1/inbound-email/?internal=true&mine=true")
        results = resp.json().get("results", resp.json())
        ids = {row["id"] for row in results}
        assert str(agent_send.id) in ids
        assert all(row["sender_type"] != "customer" for row in results)

    def test_default_listing_still_includes_customer_mail(
        self, admin_user, admin_client, tenant
    ):
        from apps.inbound_email.models import InboundEmail

        self._make(
            tenant,
            sender_type=InboundEmail.SenderType.CUSTOMER,
            direction=InboundEmail.Direction.INBOUND,
            status=InboundEmail.Status.PARKED_IN_HUB,
            recipient_email="support@kanzan.io",
            subject="Customer question",
        )

        resp = admin_client.get("/api/v1/inbound-email/")
        assert resp.status_code == 200
        results = resp.json().get("results", resp.json())
        subjects = {row["subject"] for row in results}
        assert "Customer question" in subjects
