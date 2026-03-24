"""
Inbound email processing service.

Handles the full pipeline:
1. Parse the raw webhook payload into an InboundEmail record.
2. Resolve the tenant from the recipient address.
3. Detect whether this is a new ticket or a reply to an existing one.
4. Create/update the ticket and add a comment.
5. Auto-link or auto-create the sender as a Contact.
"""

import logging
import re

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from django.core.files.storage import default_storage

from apps.comments.models import Comment
from apps.contacts.models import Contact
from apps.inbound_email.models import InboundEmail
from apps.tenants.models import Tenant
from apps.tickets.models import Ticket, TicketStatus
from main.context import set_current_tenant

logger = logging.getLogger(__name__)

# Pattern to match ticket reference in subject: [#123] or [Ticket #123]
TICKET_REF_PATTERN = re.compile(r"\[(?:Ticket\s*)?#(\d+)\]", re.IGNORECASE)


def resolve_tenant_from_address(recipient_email):
    """
    Resolve tenant from the recipient email address.

    Supports two patterns:
    - support+{tenant_slug}@domain.com  (plus-addressing)
    - {tenant_slug}@inbound.domain.com  (subdomain routing)

    Returns the Tenant or None.
    """
    local_part, _, domain = recipient_email.partition("@")

    # Pattern 1: plus-addressing (support+acme@kanzan.io)
    if "+" in local_part:
        slug = local_part.split("+", 1)[1]
        tenant = Tenant.objects.filter(slug=slug, is_active=True).first()
        if tenant:
            return tenant

    # Pattern 2: slug as local part (acme@inbound.kanzan.io)
    tenant = Tenant.objects.filter(slug=local_part, is_active=True).first()
    if tenant:
        return tenant

    # Pattern 3: tenant has a custom inbound email configured in settings
    from apps.tenants.models import TenantSettings

    settings = TenantSettings.objects.filter(
        inbound_email_address=recipient_email,
    ).select_related("tenant").first()
    if settings and settings.tenant.is_active:
        return settings.tenant

    return None


def extract_ticket_number(subject):
    """
    Extract a ticket number from the email subject line.

    Looks for patterns like [#42] or [Ticket #42] which are typically
    added to outbound notification emails.

    Returns the ticket number (int) or None.
    """
    match = TICKET_REF_PATTERN.search(subject)
    if match:
        return int(match.group(1))
    return None


def strip_quoted_reply(body_text):
    """
    Strip the quoted reply portion from an email body.

    Handles common patterns:
    - Lines starting with ">"
    - "On ... wrote:" blocks
    - "--- Original Message ---" separators
    """
    if not body_text:
        return ""

    lines = body_text.split("\n")
    result = []

    for line in lines:
        stripped = line.strip()
        # Stop at common reply delimiters
        if re.match(r"^on .+ wrote:$", stripped, re.IGNORECASE):
            break
        if stripped.startswith("---") and "original" in stripped.lower():
            break
        if stripped.startswith(">"):
            continue
        result.append(line)

    # Strip trailing whitespace
    text = "\n".join(result).rstrip()
    return text


def find_or_create_contact(tenant, sender_email, sender_name=""):
    """
    Find an existing contact by email or create a new one.

    Returns (contact, was_created).
    """
    set_current_tenant(tenant)
    contact = Contact.objects.filter(email=sender_email).first()
    if contact:
        return contact, False

    first_name = ""
    last_name = ""
    if sender_name:
        parts = sender_name.strip().split(None, 1)
        first_name = parts[0] if parts else ""
        last_name = parts[1] if len(parts) > 1 else ""

    contact = Contact(
        email=sender_email,
        first_name=first_name or sender_email.split("@")[0],
        last_name=last_name,
        tenant=tenant,
    )
    contact.save()
    logger.info("Auto-created contact %s for tenant %s", sender_email, tenant.slug)
    return contact, True


def find_existing_ticket(tenant, inbound_email):
    """
    Try to find the existing ticket this email is replying to.

    Strategy (in order):
    1. Ticket number in subject line: [#42]
    2. In-Reply-To header matching a previous outbound message
    3. References header matching a previous outbound message
    """
    set_current_tenant(tenant)

    # Strategy 1: ticket number in subject
    ticket_number = extract_ticket_number(inbound_email.subject)
    if ticket_number:
        ticket = Ticket.objects.filter(number=ticket_number).first()
        if ticket:
            logger.info(
                "Matched email to ticket #%d via subject line", ticket_number,
            )
            return ticket

    # Strategy 2: In-Reply-To header
    if inbound_email.in_reply_to:
        prev = InboundEmail.objects.filter(
            tenant=tenant,
            message_id=inbound_email.in_reply_to,
            ticket__isnull=False,
        ).select_related("ticket").first()
        if prev:
            logger.info(
                "Matched email to ticket #%d via In-Reply-To",
                prev.ticket.number,
            )
            return prev.ticket

    # Strategy 3: References header
    if inbound_email.references:
        ref_ids = inbound_email.references.split()
        for ref_id in reversed(ref_ids):  # Most recent first
            prev = InboundEmail.objects.filter(
                tenant=tenant,
                message_id=ref_id,
                ticket__isnull=False,
            ).select_related("ticket").first()
            if prev:
                logger.info(
                    "Matched email to ticket #%d via References",
                    prev.ticket.number,
                )
                return prev.ticket

    return None


@transaction.atomic
def process_inbound_email(inbound_email_id):
    """
    Process a single InboundEmail record.

    This is the main entry point called by the Celery task.
    """
    inbound = InboundEmail.objects.select_for_update().get(pk=inbound_email_id)

    if inbound.status != InboundEmail.Status.PENDING:
        logger.warning(
            "InboundEmail %s already processed (status=%s), skipping.",
            inbound.pk, inbound.status,
        )
        return

    inbound.status = InboundEmail.Status.PROCESSING
    inbound.save(update_fields=["status", "updated_at"])

    try:
        # 1. Resolve tenant
        tenant = inbound.tenant
        if not tenant:
            tenant = resolve_tenant_from_address(inbound.recipient_email)
            if not tenant:
                inbound.status = InboundEmail.Status.REJECTED
                inbound.error_message = (
                    f"Could not resolve tenant from recipient: "
                    f"{inbound.recipient_email}"
                )
                inbound.save(update_fields=["status", "error_message", "updated_at"])
                logger.warning(
                    "Rejected inbound email %s: no tenant for %s",
                    inbound.pk, inbound.recipient_email,
                )
                return
            inbound.tenant = tenant
            inbound.save(update_fields=["tenant", "updated_at"])

        set_current_tenant(tenant)

        # 2. Dedup check
        existing = InboundEmail.objects.filter(
            tenant=tenant,
            message_id=inbound.message_id,
            status__in=[
                InboundEmail.Status.TICKET_CREATED,
                InboundEmail.Status.REPLY_ADDED,
            ],
        ).exclude(pk=inbound.pk).exists()

        if existing:
            inbound.status = InboundEmail.Status.REJECTED
            inbound.error_message = "Duplicate message_id already processed."
            inbound.save(update_fields=["status", "error_message", "updated_at"])
            logger.info("Rejected duplicate inbound email %s", inbound.pk)
            return

        # 3. Find or create contact
        contact, _ = find_or_create_contact(
            tenant, inbound.sender_email, inbound.sender_name,
        )

        # 4. Is this a reply to an existing ticket?
        existing_ticket = find_existing_ticket(tenant, inbound)

        if existing_ticket:
            _add_reply_to_ticket(inbound, existing_ticket, contact)
        else:
            _create_ticket_from_email(inbound, tenant, contact)

    except Exception as exc:
        logger.exception("Failed to process inbound email %s", inbound.pk)
        inbound.status = InboundEmail.Status.FAILED
        inbound.error_message = str(exc)[:2000]
        inbound.save(update_fields=["status", "error_message", "updated_at"])
        raise


def _create_ticket_from_email(inbound, tenant, contact):
    """Create a new ticket from an inbound email."""
    set_current_tenant(tenant)

    # Get the default status for this tenant
    default_status = TicketStatus.objects.filter(is_default=True).first()
    if not default_status:
        default_status = TicketStatus.objects.first()

    if not default_status:
        raise ValueError(f"No ticket statuses configured for tenant {tenant.slug}")

    subject = inbound.subject or "(No Subject)"
    body = strip_quoted_reply(inbound.body_text) or inbound.body_text

    # Use contact's linked user if available, otherwise use a system reference
    ticket = Ticket(
        subject=subject,
        description=body,
        status=default_status,
        contact=contact,
        created_by_id=_get_system_user_id(tenant),
        tenant=tenant,
        tags=["email"],
        custom_data={"source": "email", "message_id": inbound.message_id},
    )
    ticket.save()

    inbound.ticket = ticket
    inbound.status = InboundEmail.Status.TICKET_CREATED
    inbound.save(update_fields=["ticket", "status", "updated_at"])

    # Attach any files that came with the email
    _attach_inbound_files(inbound, ticket)

    logger.info(
        "Created ticket #%d from email by %s (tenant: %s)",
        ticket.number, inbound.sender_email, tenant.slug,
    )

    # Send confirmation email to the contact
    try:
        from apps.tickets.tasks import send_ticket_created_email_task

        send_ticket_created_email_task.delay(str(ticket.pk), str(tenant.pk))
    except Exception:
        logger.exception(
            "Failed to queue ticket created email for ticket #%d",
            ticket.number,
        )

    return ticket


def _add_reply_to_ticket(inbound, ticket, contact):
    """Add a comment to an existing ticket from an inbound email reply."""
    set_current_tenant(ticket.tenant)
    body = strip_quoted_reply(inbound.body_text)

    if not body.strip():
        inbound.status = InboundEmail.Status.REJECTED
        inbound.error_message = "Empty reply body after stripping quotes."
        inbound.save(update_fields=["status", "error_message", "updated_at"])
        return

    ticket_ct = ContentType.objects.get_for_model(Ticket)

    comment = Comment(
        content_type=ticket_ct,
        object_id=ticket.pk,
        author_id=_get_system_user_id(ticket.tenant),
        body=body,
        is_internal=False,
        tenant=ticket.tenant,
    )
    comment.save()

    # Attach any files that came with the email
    _attach_inbound_files(inbound, ticket)

    inbound.ticket = ticket
    inbound.status = InboundEmail.Status.REPLY_ADDED
    inbound.save(update_fields=["ticket", "status", "updated_at"])

    logger.info(
        "Added reply to ticket #%d from %s",
        ticket.number, inbound.sender_email,
    )
    return comment


def _attach_inbound_files(inbound, ticket):
    """
    Create Attachment records for files saved during webhook handling.

    Reads attachment_metadata from the InboundEmail, opens each stored
    file, and creates an Attachment linked to the ticket. Cleans up
    temporary storage paths after successful attachment creation.
    """
    if not inbound.attachment_metadata:
        return

    from django.core.files.base import File

    from apps.attachments.services import create_attachment

    tenant = ticket.tenant
    system_user_id = _get_system_user_id(tenant)

    from apps.accounts.models import User

    system_user = User.objects.get(pk=system_user_id)

    created = 0
    for meta in inbound.attachment_metadata:
        storage_path = meta.get("storage_path")
        if not storage_path or not default_storage.exists(storage_path):
            logger.warning(
                "Inbound attachment file missing: %s (email %s)",
                storage_path, inbound.pk,
            )
            continue

        try:
            with default_storage.open(storage_path, "rb") as f:
                django_file = File(f, name=meta.get("filename", "attachment"))
                django_file.size = meta.get("size", 0) or f.size
                create_attachment(tenant, system_user, django_file, ticket)

            # Clean up the temporary file
            default_storage.delete(storage_path)
            created += 1
        except Exception:
            logger.exception(
                "Failed to create attachment from inbound file %s (email %s)",
                meta.get("filename"), inbound.pk,
            )

    if created:
        logger.info(
            "Created %d attachment(s) for ticket #%d from inbound email %s",
            created, ticket.number, inbound.pk,
        )


def _get_system_user_id(tenant):
    """
    Get a system user ID for automated ticket creation.

    Uses the first admin member of the tenant. In production, you'd
    typically have a dedicated system/bot user per tenant.
    """
    from apps.accounts.models import TenantMembership

    membership = (
        TenantMembership.objects.filter(
            tenant=tenant,
            role__hierarchy_level=10,
            is_active=True,
        )
        .select_related("user")
        .first()
    )
    if membership:
        return membership.user_id

    # Fallback: any active member
    membership = (
        TenantMembership.objects.filter(tenant=tenant, is_active=True)
        .select_related("user")
        .first()
    )
    if membership:
        return membership.user_id

    raise ValueError(f"No active users found for tenant {tenant.slug}")
