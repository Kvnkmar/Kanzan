"""
Comprehensive inbound email tests.

Covers:
- Full processing pipeline (new ticket, reply)
- Threading: In-Reply-To, References, subject fallback
- Threading priority order (headers before subject)
- Duplicate webhook delivery handling (idempotency_key)
- Loop detection (own outbound address)
- Auto-reply / OOO / bounce rejection
- Noreply sender rejection
- Multi-tenant isolation (cross-tenant safety)
- Malformed input handling
- Message-ID normalization (angle bracket consistency)
- Webhook endpoint normalization (SendGrid, Mailgun)
"""

import pytest

from conftest import (
    ContactFactory,
    InboundEmailFactory,
    MembershipFactory,
    TenantFactory,
    TicketFactory,
    TicketStatusFactory,
    UserFactory,
)
from main.context import clear_current_tenant, set_current_tenant

from apps.comments.models import Comment
from apps.inbound_email.filters import (
    check_auto_reply_headers,
    check_loop,
    check_noreply_sender,
    check_subject_auto_reply,
    run_all_filters,
)
from apps.inbound_email.models import InboundEmail
from apps.inbound_email.services import process_inbound_email
from apps.inbound_email.threading import find_existing_ticket
from apps.inbound_email.utils import (
    extract_header,
    normalize_message_id,
    normalize_references,
    strip_quoted_reply,
)


# ═══════════════════════════════════════════════════════════════════
# UTILITIES
# ═══════════════════════════════════════════════════════════════════


class TestNormalizeMessageId:
    def test_strips_angle_brackets(self):
        assert normalize_message_id("<id@host>") == "id@host"

    def test_handles_no_brackets(self):
        assert normalize_message_id("id@host") == "id@host"

    def test_handles_whitespace(self):
        assert normalize_message_id("  <id@host>  ") == "id@host"

    def test_handles_empty(self):
        assert normalize_message_id("") == ""
        assert normalize_message_id(None) == ""


class TestNormalizeReferences:
    def test_normalizes_multiple_ids(self):
        raw = "<id1@host> <id2@host> <id3@host>"
        assert normalize_references(raw) == "id1@host id2@host id3@host"

    def test_handles_empty(self):
        assert normalize_references("") == ""


class TestExtractHeader:
    def test_extracts_message_id(self):
        headers = "From: test@example.com\nMessage-ID: <abc@host>\nSubject: Test"
        assert extract_header(headers, "Message-ID") == "abc@host"

    def test_handles_folded_headers(self):
        headers = "References: <id1@host>\n <id2@host>\n <id3@host>\nSubject: Test"
        result = extract_header(headers, "References")
        assert "id1@host" in result

    def test_returns_empty_for_missing_header(self):
        headers = "From: test@example.com\nSubject: Test"
        assert extract_header(headers, "Message-ID") == ""

    def test_handles_empty_headers(self):
        assert extract_header("", "Message-ID") == ""
        assert extract_header(None, "Message-ID") == ""


class TestStripQuotedReply:
    def test_strips_gmail_forward_separator(self):
        body = "My message\n\n---------- Forwarded message ----------\nOld content"
        result = strip_quoted_reply(body)
        assert "My message" in result
        assert "Old content" not in result

    def test_strips_outlook_header_block(self):
        body = "My reply\n\nFrom: Someone <a@b.com>\nSent: Monday\n\nOriginal"
        result = strip_quoted_reply(body)
        assert "My reply" in result
        assert "Original" not in result

    def test_preserves_body_without_quotes(self):
        body = "Just a plain message with no quoting"
        assert strip_quoted_reply(body) == body


# ═══════════════════════════════════════════════════════════════════
# FILTERS
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestLoopDetection:
    @pytest.fixture(autouse=True)
    def _settings(self, settings):
        settings.DEFAULT_FROM_EMAIL = "support@kanzan.test"

    def test_rejects_own_outbound_address(self):
        inbound = InboundEmailFactory.build(sender_email="support@kanzan.test")
        should_reject, reason = check_loop(inbound)
        assert should_reject
        assert "system outbound" in reason

    def test_allows_customer_sender(self):
        inbound = InboundEmailFactory.build(sender_email="customer@example.com")
        should_reject, _ = check_loop(inbound)
        assert not should_reject


class TestNoreplySenderFilter:
    def test_rejects_noreply(self):
        inbound = InboundEmailFactory.build(sender_email="noreply@company.com")
        should_reject, _ = check_noreply_sender(inbound)
        assert should_reject

    def test_rejects_mailer_daemon(self):
        inbound = InboundEmailFactory.build(sender_email="mailer-daemon@mx.google.com")
        should_reject, _ = check_noreply_sender(inbound)
        assert should_reject

    def test_rejects_postmaster(self):
        inbound = InboundEmailFactory.build(sender_email="postmaster@company.com")
        should_reject, _ = check_noreply_sender(inbound)
        assert should_reject

    def test_allows_normal_sender(self):
        inbound = InboundEmailFactory.build(sender_email="jane@company.com")
        should_reject, _ = check_noreply_sender(inbound)
        assert not should_reject


class TestAutoReplyHeaderFilter:
    def test_rejects_auto_submitted(self):
        inbound = InboundEmailFactory.build(
            raw_headers="Auto-Submitted: auto-replied\nSubject: OOO",
        )
        should_reject, _ = check_auto_reply_headers(inbound)
        assert should_reject

    def test_allows_auto_submitted_no(self):
        inbound = InboundEmailFactory.build(
            raw_headers="Auto-Submitted: no\nSubject: Real email",
        )
        should_reject, _ = check_auto_reply_headers(inbound)
        assert not should_reject

    def test_rejects_precedence_bulk(self):
        inbound = InboundEmailFactory.build(
            raw_headers="Precedence: bulk\nSubject: Newsletter",
        )
        should_reject, _ = check_auto_reply_headers(inbound)
        assert should_reject

    def test_rejects_x_autoreply(self):
        inbound = InboundEmailFactory.build(
            raw_headers="X-Autoreply: yes\nSubject: OOO",
        )
        should_reject, _ = check_auto_reply_headers(inbound)
        assert should_reject


class TestSubjectAutoReplyFilter:
    def test_rejects_out_of_office(self):
        inbound = InboundEmailFactory.build(subject="Out of Office: Jane Doe")
        should_reject, _ = check_subject_auto_reply(inbound)
        assert should_reject

    def test_rejects_automatic_reply(self):
        inbound = InboundEmailFactory.build(subject="Automatic reply: Meeting")
        should_reject, _ = check_subject_auto_reply(inbound)
        assert should_reject

    def test_rejects_undeliverable(self):
        inbound = InboundEmailFactory.build(subject="Undeliverable: Invoice #42")
        should_reject, _ = check_subject_auto_reply(inbound)
        assert should_reject

    def test_rejects_delivery_failure(self):
        inbound = InboundEmailFactory.build(subject="Mail delivery failed: your email")
        should_reject, _ = check_subject_auto_reply(inbound)
        assert should_reject

    def test_allows_normal_subject(self):
        inbound = InboundEmailFactory.build(subject="Re: [#42] Need help")
        should_reject, _ = check_subject_auto_reply(inbound)
        assert not should_reject


@pytest.mark.django_db
class TestRunAllFilters:
    @pytest.fixture(autouse=True)
    def _settings(self, settings):
        settings.DEFAULT_FROM_EMAIL = "support@kanzan.test"

    def test_loop_rejected(self):
        inbound = InboundEmailFactory.build(sender_email="support@kanzan.test")
        should_reject, _ = run_all_filters(inbound)
        assert should_reject

    def test_ooo_rejected(self):
        inbound = InboundEmailFactory.build(subject="Out of Office: I'm away")
        should_reject, _ = run_all_filters(inbound)
        assert should_reject

    def test_normal_email_passes(self):
        inbound = InboundEmailFactory.build(
            sender_email="customer@example.com",
            subject="Help me please",
        )
        should_reject, _ = run_all_filters(inbound)
        assert not should_reject


# ═══════════════════════════════════════════════════════════════════
# THREADING
# ═══════════════════════════════════════════════════════════════════


def _setup_tenant_with_ticket():
    """Helper: create a tenant with admin, default status, and a ticket."""
    tenant = TenantFactory(slug="thread-test")
    user = UserFactory()
    from apps.accounts.models import Role
    admin_role = Role.unscoped.get(tenant=tenant, slug="admin")
    MembershipFactory(user=user, tenant=tenant, role=admin_role)

    set_current_tenant(tenant)
    status = TicketStatusFactory(tenant=tenant, is_default=True, name="Open", slug="open")
    ticket = TicketFactory(tenant=tenant, status=status, created_by=user)
    clear_current_tenant()
    return tenant, user, ticket


@pytest.mark.django_db
class TestThreadingByInReplyTo:
    def test_matches_by_in_reply_to(self):
        tenant, user, ticket = _setup_tenant_with_ticket()

        # Outbound record exists for this ticket
        InboundEmail.objects.create(
            tenant=tenant, ticket=ticket,
            message_id="outbound-msg-001@thread-test.kanzan.test",
            sender_email="support@kanzan.test",
            recipient_email="customer@example.com",
            direction=InboundEmail.Direction.OUTBOUND,
            status=InboundEmail.Status.SENT,
        )

        # Inbound reply references that outbound message
        inbound = InboundEmailFactory(
            tenant=tenant,
            in_reply_to="outbound-msg-001@thread-test.kanzan.test",
            subject="Re: something",
        )

        set_current_tenant(tenant)
        result = find_existing_ticket(tenant, inbound)
        clear_current_tenant()
        assert result == ticket


@pytest.mark.django_db
class TestThreadingByReferences:
    def test_matches_by_references(self):
        tenant, user, ticket = _setup_tenant_with_ticket()

        InboundEmail.objects.create(
            tenant=tenant, ticket=ticket,
            message_id="ref-msg-001@thread-test.kanzan.test",
            sender_email="support@kanzan.test",
            recipient_email="customer@example.com",
            direction=InboundEmail.Direction.OUTBOUND,
            status=InboundEmail.Status.SENT,
        )

        inbound = InboundEmailFactory(
            tenant=tenant, in_reply_to="",
            references="some-old@id ref-msg-001@thread-test.kanzan.test",
            subject="Something unrelated",
        )

        set_current_tenant(tenant)
        result = find_existing_ticket(tenant, inbound)
        clear_current_tenant()
        assert result == ticket


@pytest.mark.django_db
class TestThreadingBySubjectFallback:
    def test_matches_by_subject_when_no_headers(self):
        tenant, user, ticket = _setup_tenant_with_ticket()

        inbound = InboundEmailFactory(
            tenant=tenant, in_reply_to="", references="",
            subject=f"Re: [#{ticket.number}] Something",
        )

        set_current_tenant(tenant)
        result = find_existing_ticket(tenant, inbound)
        clear_current_tenant()
        assert result == ticket

    def test_headers_take_priority_over_subject(self):
        """In-Reply-To match wins even if subject has a different ticket number."""
        tenant, user, ticket1 = _setup_tenant_with_ticket()

        set_current_tenant(tenant)
        status = TicketStatusFactory(
            tenant=tenant, is_default=False, name="New", slug="new",
        )
        ticket2 = TicketFactory(tenant=tenant, status=status, created_by=user)
        clear_current_tenant()

        InboundEmail.objects.create(
            tenant=tenant, ticket=ticket1,
            message_id="header-match@test",
            sender_email="support@kanzan.test",
            recipient_email="customer@example.com",
            direction=InboundEmail.Direction.OUTBOUND,
            status=InboundEmail.Status.SENT,
        )

        # Subject references ticket2, but In-Reply-To references ticket1
        inbound = InboundEmailFactory(
            tenant=tenant,
            in_reply_to="header-match@test",
            subject=f"Re: [#{ticket2.number}] Wrong ticket",
        )

        set_current_tenant(tenant)
        result = find_existing_ticket(tenant, inbound)
        clear_current_tenant()
        assert result == ticket1, "In-Reply-To should take priority over subject"


# ═══════════════════════════════════════════════════════════════════
# FULL PIPELINE
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestInboundProcessingPipeline:
    @pytest.fixture(autouse=True)
    def _settings(self, settings):
        settings.DEFAULT_FROM_EMAIL = "support@kanzan.test"

    def test_creates_ticket_from_new_email(self):
        tenant, user, _ = _setup_tenant_with_ticket()

        inbound = InboundEmailFactory(
            recipient_email="support+thread-test@kanzan.io",
            tenant=tenant,
            subject="New issue",
            body_text="Please help.",
        )
        process_inbound_email(inbound.pk)

        inbound.refresh_from_db()
        assert inbound.status == "ticket_created"
        assert inbound.ticket is not None
        assert inbound.idempotency_key is not None

    def test_reply_attaches_to_existing_ticket(self):
        tenant, user, ticket = _setup_tenant_with_ticket()

        # Create a prior outbound email for threading
        InboundEmail.objects.create(
            tenant=tenant, ticket=ticket,
            message_id="prior-outbound@thread-test.kanzan.test",
            sender_email="support@kanzan.test",
            recipient_email="customer@example.com",
            direction=InboundEmail.Direction.OUTBOUND,
            status=InboundEmail.Status.SENT,
        )

        inbound = InboundEmailFactory(
            recipient_email="support+thread-test@kanzan.io",
            tenant=tenant,
            in_reply_to="prior-outbound@thread-test.kanzan.test",
            subject="Re: Something",
            body_text="Thanks for the update.",
        )
        process_inbound_email(inbound.pk)

        inbound.refresh_from_db()
        assert inbound.status == "reply_added"
        assert inbound.ticket == ticket

        # A Comment should have been created (use unscoped to avoid
        # tenant context requirement outside the processing pipeline)
        from django.contrib.contenttypes.models import ContentType
        from apps.tickets.models import Ticket
        ct = ContentType.objects.get_for_model(Ticket)
        assert Comment.unscoped.filter(
            content_type=ct, object_id=ticket.pk,
        ).exists()

    def test_loop_detection_rejects_own_sender(self):
        tenant, user, _ = _setup_tenant_with_ticket()

        inbound = InboundEmailFactory(
            recipient_email="support+thread-test@kanzan.io",
            sender_email="support@kanzan.test",
            tenant=tenant,
            subject="Bounced confirmation",
            body_text="Auto-generated.",
        )
        process_inbound_email(inbound.pk)

        inbound.refresh_from_db()
        assert inbound.status == "rejected"
        assert "system outbound" in inbound.error_message

    def test_ooo_auto_reply_rejected(self):
        tenant, user, _ = _setup_tenant_with_ticket()

        inbound = InboundEmailFactory(
            recipient_email="support+thread-test@kanzan.io",
            tenant=tenant,
            subject="Out of Office: I'm on vacation",
            body_text="I will return on Monday.",
        )
        process_inbound_email(inbound.pk)

        inbound.refresh_from_db()
        assert inbound.status == "rejected"

    def test_mailer_daemon_rejected(self):
        tenant, user, _ = _setup_tenant_with_ticket()

        inbound = InboundEmailFactory(
            recipient_email="support+thread-test@kanzan.io",
            sender_email="mailer-daemon@mx.google.com",
            tenant=tenant,
            subject="Mail delivery failed",
            body_text="550 User unknown",
        )
        process_inbound_email(inbound.pk)

        inbound.refresh_from_db()
        assert inbound.status == "rejected"


@pytest.mark.django_db
class TestDuplicateWebhookDelivery:
    def test_duplicate_message_id_rejected(self):
        tenant, user, _ = _setup_tenant_with_ticket()

        inbound1 = InboundEmailFactory(
            recipient_email="support+thread-test@kanzan.io",
            tenant=tenant, message_id="dup-msg-123@example.com",
            subject="Help", body_text="First delivery",
        )
        process_inbound_email(inbound1.pk)
        inbound1.refresh_from_db()
        assert inbound1.status == "ticket_created"

        # Second delivery of the same message_id
        inbound2 = InboundEmailFactory(
            recipient_email="support+thread-test@kanzan.io",
            tenant=tenant, message_id="dup-msg-123@example.com",
            subject="Help", body_text="Second delivery",
        )
        process_inbound_email(inbound2.pk)
        inbound2.refresh_from_db()
        assert inbound2.status == "rejected"
        assert "Duplicate" in inbound2.error_message

    def test_processing_status_allows_retry(self):
        """A record stuck in PROCESSING can be retried."""
        tenant, user, _ = _setup_tenant_with_ticket()

        inbound = InboundEmailFactory(
            recipient_email="support+thread-test@kanzan.io",
            tenant=tenant, subject="Retry me", body_text="Content",
            status="processing",
        )
        process_inbound_email(inbound.pk)

        inbound.refresh_from_db()
        assert inbound.status == "ticket_created"


# ═══════════════════════════════════════════════════════════════════
# MULTI-TENANT ISOLATION
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestMultiTenantIsolation:
    def test_same_subject_different_tenants_no_cross_attach(self):
        """[#1] in subject does NOT cross-attach to another tenant's ticket #1."""
        tenant_a, user_a, ticket_a = _setup_tenant_with_ticket()

        tenant_b = TenantFactory(slug="tenant-iso-b")
        user_b = UserFactory()
        from apps.accounts.models import Role
        admin_b = Role.unscoped.get(tenant=tenant_b, slug="admin")
        MembershipFactory(user=user_b, tenant=tenant_b, role=admin_b)
        set_current_tenant(tenant_b)
        TicketStatusFactory(tenant=tenant_b, is_default=True, name="Open", slug="open")
        clear_current_tenant()

        # Email addressed to tenant_b but subject mentions [#1] (ticket_a's number)
        inbound = InboundEmailFactory(
            recipient_email="support+tenant-iso-b@kanzan.io",
            tenant=tenant_b,
            subject=f"Re: [#{ticket_a.number}] Old ticket",
            body_text="This is for tenant B",
            in_reply_to="", references="",
        )
        process_inbound_email(inbound.pk)

        inbound.refresh_from_db()
        if inbound.ticket:
            # If it matched, it should be a ticket in tenant_b, NOT tenant_a
            assert inbound.ticket.tenant == tenant_b

    def test_same_message_id_different_tenants_no_cross_attach(self):
        """Message-ID collision across tenants does not cause cross-attach."""
        tenant_a, user_a, ticket_a = _setup_tenant_with_ticket()

        # Create a record in tenant_a
        InboundEmail.objects.create(
            tenant=tenant_a, ticket=ticket_a,
            message_id="shared-msg@example.com",
            sender_email="support@kanzan.test",
            recipient_email="customer@example.com",
            direction=InboundEmail.Direction.OUTBOUND,
            status=InboundEmail.Status.SENT,
        )

        # Set up tenant_b
        tenant_b = TenantFactory(slug="tenant-iso-c")
        user_b = UserFactory()
        from apps.accounts.models import Role
        admin_b = Role.unscoped.get(tenant=tenant_b, slug="admin")
        MembershipFactory(user=user_b, tenant=tenant_b, role=admin_b)
        set_current_tenant(tenant_b)
        TicketStatusFactory(tenant=tenant_b, is_default=True, name="Open", slug="open-b")
        clear_current_tenant()

        # Email to tenant_b with In-Reply-To matching tenant_a's record
        inbound = InboundEmailFactory(
            recipient_email="support+tenant-iso-c@kanzan.io",
            tenant=tenant_b,
            in_reply_to="shared-msg@example.com",
            subject="Cross-tenant test",
            body_text="Content",
        )
        process_inbound_email(inbound.pk)

        inbound.refresh_from_db()
        # Should NOT attach to tenant_a's ticket
        if inbound.ticket:
            assert inbound.ticket.tenant == tenant_b

    def test_wrong_tenant_resolution_does_not_create_ticket(self):
        """Email addressed to a non-existent tenant is rejected."""
        inbound = InboundEmailFactory(
            recipient_email="support+nonexistent@kanzan.io",
            tenant=None,
            subject="Lost email",
            body_text="Content",
        )
        process_inbound_email(inbound.pk)

        inbound.refresh_from_db()
        assert inbound.status == "rejected"
        assert inbound.ticket is None


# ═══════════════════════════════════════════════════════════════════
# MALFORMED INPUT
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestMalformedInput:
    def test_empty_body_reply_rejected(self):
        """Reply with only quoted content (empty after stripping) is rejected."""
        tenant, user, ticket = _setup_tenant_with_ticket()

        InboundEmail.objects.create(
            tenant=tenant, ticket=ticket,
            message_id="prior@test",
            sender_email="support@kanzan.test",
            recipient_email="customer@example.com",
            direction=InboundEmail.Direction.OUTBOUND,
            status=InboundEmail.Status.SENT,
        )

        inbound = InboundEmailFactory(
            recipient_email="support+thread-test@kanzan.io",
            tenant=tenant,
            in_reply_to="prior@test",
            subject="Re: [#1] Something",
            body_text="> Only quoted content\n> Nothing new here",
        )
        process_inbound_email(inbound.pk)

        inbound.refresh_from_db()
        assert inbound.status == "rejected"
        assert "Empty reply" in inbound.error_message

    def test_empty_subject_creates_ticket_with_placeholder(self):
        """Email with no subject still creates a ticket."""
        tenant, user, _ = _setup_tenant_with_ticket()

        inbound = InboundEmailFactory(
            recipient_email="support+thread-test@kanzan.io",
            tenant=tenant, subject="",
            body_text="I forgot the subject.",
        )
        process_inbound_email(inbound.pk)

        inbound.refresh_from_db()
        assert inbound.status == "ticket_created"
        assert inbound.ticket.subject == "(No Subject)"


# ═══════════════════════════════════════════════════════════════════
# WEBHOOK NORMALIZATION
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestWebhookNormalization:
    def test_sendgrid_webhook_normalizes_ids(self, client, settings):
        """SendGrid webhook strips <> from Message-ID."""
        settings.INBOUND_EMAIL_WEBHOOK_SECRET = "testsecret"
        resp = client.post(
            "/inbound/email/sendgrid/?secret=testsecret",
            data={
                "from": "Jane Doe <jane@example.com>",
                "to": "support+demo@kanzan.io",
                "subject": "Test",
                "text": "Hello",
                "headers": "Message-ID: <abc123@example.com>\nIn-Reply-To: <reply-to-id@example.com>",
            },
        )
        assert resp.status_code == 200
        inbound = InboundEmail.objects.latest("created_at")
        assert inbound.message_id == "abc123@example.com"
        assert inbound.in_reply_to == "reply-to-id@example.com"
        assert inbound.sender_name == "Jane Doe"
        assert inbound.sender_email == "jane@example.com"

    def test_mailgun_webhook_normalizes_ids(self, client, settings):
        """Mailgun webhook strips <> from Message-Id POST field."""
        settings.INBOUND_EMAIL_WEBHOOK_SECRET = "testsecret"
        resp = client.post(
            "/inbound/email/mailgun/?secret=testsecret",
            data={
                "from": "John <john@example.com>",
                "sender": "john@example.com",
                "recipient": "support+demo@kanzan.io",
                "subject": "Test",
                "body-plain": "Hello",
                "Message-Id": "<mailgun-id-456@example.com>",
                "In-Reply-To": "<reply-to-789@example.com>",
                "References": "<ref1@host> <ref2@host>",
            },
        )
        assert resp.status_code == 200
        inbound = InboundEmail.objects.latest("created_at")
        # All IDs should be normalized (no angle brackets)
        assert inbound.message_id == "mailgun-id-456@example.com"
        assert inbound.in_reply_to == "reply-to-789@example.com"
        assert inbound.references == "ref1@host ref2@host"
        assert inbound.direction == InboundEmail.Direction.INBOUND

    def test_sendgrid_sets_direction_and_sender_type(self, client, settings):
        """Webhook records are marked as inbound/customer."""
        settings.INBOUND_EMAIL_WEBHOOK_SECRET = "testsecret"
        client.post(
            "/inbound/email/sendgrid/?secret=testsecret",
            data={
                "from": "customer@test.com",
                "to": "support@kanzan.io",
                "subject": "Test",
                "text": "Hello",
                "headers": "",
            },
        )
        inbound = InboundEmail.objects.latest("created_at")
        assert inbound.direction == InboundEmail.Direction.INBOUND
        assert inbound.sender_type == InboundEmail.SenderType.CUSTOMER


# ═══════════════════════════════════════════════════════════════════
# SECURITY
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestSecurityHeaders:
    def test_header_injection_in_subject_safe(self):
        """Subject with newlines does not corrupt ticket."""
        tenant, user, _ = _setup_tenant_with_ticket()

        inbound = InboundEmailFactory(
            recipient_email="support+thread-test@kanzan.io",
            tenant=tenant,
            subject="Normal subject\r\nBcc: evil@attacker.com",
            body_text="Content",
        )
        process_inbound_email(inbound.pk)

        inbound.refresh_from_db()
        assert inbound.status == "ticket_created"
        # The injected header should be part of the subject, not a real header
        assert "evil@attacker.com" in inbound.ticket.subject

    def test_spoofed_tenant_in_headers_ignored(self):
        """X-Tenant-Slug in inbound headers does not override tenant resolution."""
        tenant_a, user_a, _ = _setup_tenant_with_ticket()

        # Email claims to be for "evil-tenant" in headers but addressed to tenant_a
        inbound = InboundEmailFactory(
            recipient_email="support+thread-test@kanzan.io",
            tenant=tenant_a,
            raw_headers="X-Tenant-Slug: evil-tenant\nMessage-ID: <x@test>",
            subject="Spoofed",
            body_text="Content",
        )
        process_inbound_email(inbound.pk)

        inbound.refresh_from_db()
        assert inbound.status == "ticket_created"
        # Ticket should be in the correct tenant, not the spoofed one
        assert inbound.ticket.tenant == tenant_a
