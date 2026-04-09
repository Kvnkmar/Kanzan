"""
Tests for hard-bounce email handling.

Validates that:
- Hard bounce email: no Contact created, BounceLog written
- Auto-reply email: no Contact created, no BounceLog (silently rejected)
- Bounce reply to existing ticket: BounceLog linked to ticket, ticket not reopened
- Legitimate email after prior bounce: Contact created normally
"""

import pytest

from conftest import (
    InboundEmailFactory,
    MembershipFactory,
    TenantFactory,
    TicketFactory,
    TicketStatusFactory,
    UserFactory,
)
from main.context import clear_current_tenant, set_current_tenant

from apps.contacts.models import Contact
from apps.inbound_email.models import BounceLog, InboundEmail
from apps.inbound_email.services import process_inbound_email


def _setup_tenant():
    """Create a tenant with admin user and default status."""
    tenant = TenantFactory(slug="bounce-test")
    set_current_tenant(tenant)

    admin = UserFactory()
    from apps.accounts.models import Role
    admin_role = Role.unscoped.get(tenant=tenant, slug="admin")
    MembershipFactory(user=admin, tenant=tenant, role=admin_role)

    TicketStatusFactory(
        tenant=tenant, name="Open", slug="open", is_default=True,
    )
    return tenant, admin


@pytest.mark.django_db
class TestHardBounceNoConcatCreated:
    """Hard bounce email: no Contact created, BounceLog written."""

    def test_mailer_daemon_bounce(self):
        tenant, _ = _setup_tenant()

        inbound = InboundEmailFactory(
            sender_email="mailer-daemon@mail.example.com",
            recipient_email=f"support+bounce-test@kanzan.io",
            subject="Mail delivery failed: returning message to sender",
            body_text="Delivery to the following recipients failed permanently.",
            raw_headers="X-Failed-Recipients: customer@example.com\n",
        )

        process_inbound_email(str(inbound.pk))
        clear_current_tenant()

        inbound.refresh_from_db()
        assert inbound.status == InboundEmail.Status.BOUNCED

        # No Contact should have been created for the mailer-daemon
        assert not Contact.unscoped.filter(email="mailer-daemon@mail.example.com").exists()
        # No Contact should have been created for the original recipient either
        # (bounce filter runs before contact creation)

        # BounceLog should exist
        bounce = BounceLog.objects.filter(inbound_email=inbound).first()
        assert bounce is not None
        assert bounce.from_address == "mailer-daemon@mail.example.com"
        assert bounce.to_address == "customer@example.com"  # from X-Failed-Recipients
        assert "noreply" in bounce.bounce_reason.lower() or "mailer-daemon" in bounce.bounce_reason.lower()

    def test_bounce_subject_detection(self):
        tenant, _ = _setup_tenant()

        inbound = InboundEmailFactory(
            sender_email="postmaster@example.com",
            recipient_email=f"support+bounce-test@kanzan.io",
            subject="Undeliverable: Re: [#1] Your ticket update",
            body_text="Your message could not be delivered.",
        )

        process_inbound_email(str(inbound.pk))
        clear_current_tenant()

        inbound.refresh_from_db()
        assert inbound.status == InboundEmail.Status.BOUNCED

        assert BounceLog.objects.filter(inbound_email=inbound).exists()


@pytest.mark.django_db
class TestAutoReplyNoBounceLog:
    """Auto-reply email: no Contact created, no BounceLog (silently rejected)."""

    def test_out_of_office_no_bouncelog(self):
        tenant, _ = _setup_tenant()

        inbound = InboundEmailFactory(
            sender_email="person@example.com",
            recipient_email=f"support+bounce-test@kanzan.io",
            subject="Out of Office: Re: Your request",
            body_text="I am currently out of the office.",
        )

        process_inbound_email(str(inbound.pk))
        clear_current_tenant()

        inbound.refresh_from_db()
        # Should be REJECTED, not BOUNCED
        assert inbound.status == InboundEmail.Status.REJECTED

        # No Contact created
        assert not Contact.unscoped.filter(email="person@example.com").exists()

        # No BounceLog written for auto-replies
        assert not BounceLog.objects.filter(inbound_email=inbound).exists()

    def test_auto_submitted_header_no_bouncelog(self):
        tenant, _ = _setup_tenant()

        inbound = InboundEmailFactory(
            sender_email="colleague@example.com",
            recipient_email=f"support+bounce-test@kanzan.io",
            subject="Re: Meeting tomorrow",
            body_text="Auto-reply: I'll get back to you.",
            raw_headers="Auto-Submitted: auto-replied\n",
        )

        process_inbound_email(str(inbound.pk))
        clear_current_tenant()

        inbound.refresh_from_db()
        assert inbound.status == InboundEmail.Status.REJECTED
        assert not BounceLog.objects.filter(inbound_email=inbound).exists()


@pytest.mark.django_db
class TestBounceLinkedToTicket:
    """Bounce reply to existing ticket: BounceLog linked to ticket, ticket not reopened."""

    def test_bounce_linked_to_ticket_no_reopen(self):
        tenant, admin = _setup_tenant()

        resolved_status = TicketStatusFactory(
            tenant=tenant, name="Resolved", slug="resolved", is_closed=True,
        )

        ticket = TicketFactory(
            tenant=tenant,
            status=resolved_status,
            created_by=admin,
            number=42,
        )

        # Simulate a bounce reply to ticket #42
        inbound = InboundEmailFactory(
            sender_email="mailer-daemon@example.com",
            recipient_email=f"support+bounce-test@kanzan.io",
            subject="Mail delivery failed: Re: [#42] Original subject",
            body_text="Delivery failed permanently.",
            raw_headers="X-Failed-Recipients: someone@bad.com\n",
        )

        process_inbound_email(str(inbound.pk))
        clear_current_tenant()

        inbound.refresh_from_db()
        assert inbound.status == InboundEmail.Status.BOUNCED

        # BounceLog should be linked to the ticket
        bounce = BounceLog.objects.filter(inbound_email=inbound).first()
        assert bounce is not None
        assert bounce.ticket_id == ticket.pk

        # Ticket should NOT be reopened (still resolved)
        ticket.refresh_from_db()
        assert ticket.status.slug == "resolved"


@pytest.mark.django_db
class TestBounceContactFlagged:
    """When a bounce is for an existing contact, flag email_bouncing=True."""

    def test_existing_contact_flagged(self):
        tenant, _ = _setup_tenant()

        # Pre-create a contact that will be the bounce target
        contact = Contact(
            email="customer@example.com",
            first_name="Jane",
            last_name="Doe",
            tenant=tenant,
        )
        contact.save()
        assert contact.email_bouncing is False

        inbound = InboundEmailFactory(
            sender_email="mailer-daemon@mail.example.com",
            recipient_email=f"support+bounce-test@kanzan.io",
            subject="Returned mail: see transcript for details",
            body_text="550 User not found",
            raw_headers="X-Failed-Recipients: customer@example.com\n",
        )

        process_inbound_email(str(inbound.pk))
        clear_current_tenant()

        contact.refresh_from_db()
        assert contact.email_bouncing is True


@pytest.mark.django_db
class TestLegitimateEmailAfterBounce:
    """Legitimate email after prior bounce: Contact created normally."""

    def test_legitimate_email_creates_contact(self):
        tenant, _ = _setup_tenant()

        # First: a bounce for this address
        bounce_email = InboundEmailFactory(
            sender_email="mailer-daemon@mail.example.com",
            recipient_email=f"support+bounce-test@kanzan.io",
            subject="Mail delivery failed",
            body_text="Delivery failed.",
            raw_headers="X-Failed-Recipients: newuser@example.com\n",
        )
        process_inbound_email(str(bounce_email.pk))

        # No contact created for the bounce
        assert not Contact.unscoped.filter(email="newuser@example.com").exists()

        # Now: a legitimate email from the same address
        legit_email = InboundEmailFactory(
            sender_email="newuser@example.com",
            sender_name="New User",
            recipient_email=f"support+bounce-test@kanzan.io",
            subject="Help with my account",
            body_text="I need help resetting my password.",
        )
        process_inbound_email(str(legit_email.pk))
        clear_current_tenant()

        legit_email.refresh_from_db()
        assert legit_email.status == InboundEmail.Status.TICKET_CREATED

        # Contact should now exist
        contact = Contact.unscoped.filter(email="newuser@example.com").first()
        assert contact is not None
        assert contact.first_name == "New"
