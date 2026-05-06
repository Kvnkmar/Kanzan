"""
Inbound email processing service.

Handles the full pipeline:
1. Filter out loops, auto-replies, and bounces.
2. Resolve the tenant from the recipient address.
3. Deduplicate via idempotency_key.
4. Detect whether this is a new ticket or a reply to an existing one.
5. Create/update the ticket and add a comment.
6. Auto-link or auto-create the sender as a Contact.
7. Process attachments.

All database writes happen inside a single atomic transaction.
Celery task dispatch uses transaction.on_commit() to prevent
queuing work for rolled-back data.
"""

import logging

from django.contrib.contenttypes.models import ContentType
from django.core.files.storage import default_storage
from django.db import IntegrityError, transaction

from apps.comments.models import Comment
from apps.contacts.models import Contact
from apps.inbound_email.filters import classify_email, run_all_filters
from apps.inbound_email.models import BounceLog, InboundEmail
from apps.inbound_email.threading import (  # noqa: F401 — re-exported for backward compat
    extract_ticket_number,
    find_existing_ticket,
)
from apps.inbound_email.utils import strip_quoted_reply
from apps.tenants.models import Tenant
from apps.tickets.models import Ticket, TicketStatus
from main.context import tenant_context

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Outbound logging (used by notifications + any non-ticket outbound path)
# ---------------------------------------------------------------------------


def log_outbound_email(
    *,
    tenant,
    recipient_email,
    subject,
    body_text="",
    message_id=None,
    ticket=None,
    sender_type=None,
):
    """
    Record an outbound email as an InboundEmail(direction=OUTBOUND) row.

    This is the generic ``ticket``-optional logger used by notification
    emails, test sends, and any other outbound path that isn't tied to
    a specific ticket. Ticket-scoped ticket-reply emails have their own
    wrapper in ``apps.tickets.email_service.record_outbound_email`` that
    also computes a deterministic idempotency key.

    Args:
        tenant: the Tenant the email was sent from (required).
        recipient_email: where the message was sent to.
        subject: the subject line (``\r\n`` are stripped for safety).
        body_text: plain-text body (optional).
        message_id: RFC 2822 Message-ID without angle brackets.
                    A random UUID is generated if omitted.
        ticket: optional Ticket the email relates to.
        sender_type: one of ``InboundEmail.SenderType`` values;
                     defaults to ``SYSTEM``.

    Returns:
        The created ``InboundEmail`` record, or None if creation failed.
    """
    import uuid as _uuid

    from django.conf import settings as dj_settings
    from django.db import IntegrityError

    from apps.inbound_email.utils import normalize_message_id

    if tenant is None:
        return None

    if message_id is None:
        base_domain = getattr(dj_settings, "BASE_DOMAIN", "localhost")
        message_id = f"out-{_uuid.uuid4().hex}@{base_domain}"

    safe_subject = (subject or "").replace("\r", "").replace("\n", " ")
    default_from = getattr(dj_settings, "DEFAULT_FROM_EMAIL", "")

    try:
        return InboundEmail.objects.create(
            tenant=tenant,
            message_id=normalize_message_id(message_id),
            sender_email=default_from,
            recipient_email=recipient_email or "",
            subject=safe_subject,
            body_text=body_text or "",
            direction=InboundEmail.Direction.OUTBOUND,
            sender_type=sender_type or InboundEmail.SenderType.SYSTEM,
            status=InboundEmail.Status.SENT,
            ticket=ticket,
        )
    except IntegrityError:
        logger.warning(
            "Outbound log collision for message_id=%s recipient=%s",
            message_id,
            recipient_email,
        )
        return None
    except Exception:
        logger.exception(
            "Failed to log outbound email to %s (subject=%r)",
            recipient_email,
            safe_subject,
        )
        return None


# ---------------------------------------------------------------------------
# Tenant resolution
# ---------------------------------------------------------------------------


def resolve_tenant_from_address(recipient_email):
    """
    Resolve tenant from the recipient email address.

    Strategies (in order):
    1. Plus-addressing: support+{slug}@domain.com
    2. Slug as local part: {slug}@inbound.domain.com
    3. Custom inbound address in TenantSettings

    Returns the Tenant or None.
    """
    local_part, _, domain = recipient_email.partition("@")

    # Strategy 1: plus-addressing (support+acme@kanzen.io)
    if "+" in local_part:
        slug = local_part.split("+", 1)[1]
        tenant = Tenant.objects.filter(slug=slug, is_active=True).first()
        if tenant:
            return tenant

    # Strategy 2: slug as local part (acme@inbound.kanzen.io)
    tenant = Tenant.objects.filter(slug=local_part, is_active=True).first()
    if tenant:
        return tenant

    # Strategy 3: custom inbound address in TenantSettings
    from apps.tenants.models import TenantSettings

    ts = TenantSettings.objects.filter(
        inbound_email_address=recipient_email,
    ).select_related("tenant").first()
    if ts and ts.tenant.is_active:
        return ts.tenant

    return None


# ---------------------------------------------------------------------------
# Contact resolution
# ---------------------------------------------------------------------------


def find_or_create_contact(tenant, sender_email, sender_name=""):
    """
    Find an existing contact by email or create a new one.

    Must be called inside a tenant_context(). Returns (contact, was_created).
    """
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


# ---------------------------------------------------------------------------
# System user resolution (cached per call)
# ---------------------------------------------------------------------------


def get_system_user(tenant):
    """
    Get a system user for automated ticket/comment creation.

    Returns the User instance (not just the ID) so callers can pass
    it to both ticket creation and attachment creation without
    re-querying.

    Uses the first admin member of the tenant, falling back to any
    active member.
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
        return membership.user

    membership = (
        TenantMembership.objects.filter(tenant=tenant, is_active=True)
        .select_related("user")
        .first()
    )
    if membership:
        return membership.user

    raise ValueError(f"No active users found for tenant {tenant.slug}")


# ---------------------------------------------------------------------------
# Main processing pipeline
# ---------------------------------------------------------------------------


@transaction.atomic
def process_inbound_email(inbound_email_id):
    """
    Process a single InboundEmail record.

    This is the main entry point called by the Celery task.
    Runs inside a single database transaction.

    Pipeline steps:
    1. Lock the record (select_for_update)
    2. Guard: reject if already processed
    3. Filter: loop detection, auto-reply, bounce
    4. Resolve tenant
    5. Deduplicate via idempotency_key
    6. Find or create contact
    7. Match to existing ticket (threading) or create new ticket
    8. Process attachments
    9. Queue outbound confirmation (via on_commit)
    """
    inbound = InboundEmail.objects.select_for_update().get(pk=inbound_email_id)

    # Guard: allow retry of PROCESSING (worker killed mid-transaction)
    if inbound.status not in (
        InboundEmail.Status.PENDING,
        InboundEmail.Status.PROCESSING,
    ):
        logger.warning(
            "InboundEmail %s already processed (status=%s), skipping.",
            inbound.pk,
            inbound.status,
        )
        return

    inbound.status = InboundEmail.Status.PROCESSING
    inbound.save(update_fields=["status", "updated_at"])

    try:
        # Step 1: Run filters BEFORE tenant resolution (cheap, no DB)
        # This ensures no Contact or Ticket is created for bounces/auto-replies.
        should_reject, reason = run_all_filters(inbound)
        if should_reject:
            classification = classify_email(inbound)
            if classification == "bounce":
                _handle_bounce(inbound, reason)
            else:
                _reject(inbound, reason)
            return

        # Step 2: Resolve tenant
        tenant = inbound.tenant
        if not tenant:
            tenant = resolve_tenant_from_address(inbound.recipient_email)
            if not tenant:
                _reject(
                    inbound,
                    f"Could not resolve tenant from recipient: "
                    f"{inbound.recipient_email}",
                )
                return
            inbound.tenant = tenant
            inbound.save(update_fields=["tenant", "updated_at"])

        # All subsequent queries use tenant-scoped managers
        with tenant_context(tenant):
            # Step 3: Idempotency check via DB unique constraint
            idem_key = f"in:{tenant.pk}:{inbound.message_id}"
            duplicate = (
                InboundEmail.objects.filter(idempotency_key=idem_key)
                .exclude(pk=inbound.pk)
                .exists()
            )
            if duplicate:
                _reject(inbound, "Duplicate message_id already processed.")
                return

            # Claim this idempotency key
            inbound.idempotency_key = idem_key
            try:
                inbound.save(update_fields=["idempotency_key", "updated_at"])
            except IntegrityError:
                # Race condition: another worker claimed the key between
                # the check and the save. This is fine — reject as duplicate.
                _reject(inbound, "Duplicate message_id (race condition).")
                return

            # Step 4: Resolve system user (cached for this entire pipeline)
            system_user = get_system_user(tenant)

            # Step 5: Find or create contact
            contact, _ = find_or_create_contact(
                tenant, inbound.sender_email, inbound.sender_name,
            )

            # Step 6: Match to existing ticket or create new one
            existing_ticket = find_existing_ticket(tenant, inbound)

            if existing_ticket:
                _add_reply_to_ticket(inbound, existing_ticket, contact, system_user)
            else:
                _create_ticket_from_email(inbound, tenant, contact, system_user)

    except Exception as exc:
        logger.exception("Failed to process inbound email %s", inbound.pk)
        # Mark as FAILED only on the final retry attempt — leave as
        # PROCESSING for earlier attempts so the Celery retry mechanism
        # can re-process it (the status guard allows PROCESSING).
        inbound.status = InboundEmail.Status.FAILED
        inbound.error_message = str(exc)
        inbound.save(update_fields=["status", "error_message", "updated_at"])
        raise


# ---------------------------------------------------------------------------
# Ticket creation / reply
# ---------------------------------------------------------------------------


def _create_ticket_from_email(inbound, tenant, contact, system_user):
    """Create a new ticket from an inbound email."""
    default_status = TicketStatus.objects.filter(is_default=True).first()
    if not default_status:
        default_status = TicketStatus.objects.first()
    if not default_status:
        raise ValueError(f"No ticket statuses configured for tenant {tenant.slug}")

    subject = (inbound.subject or "(No Subject)").replace("\r", "").replace("\n", " ")
    body = strip_quoted_reply(inbound.body_text) or inbound.body_text

    ticket = Ticket(
        subject=subject,
        description=body,
        status=default_status,
        contact=contact,
        created_by=system_user,
        tenant=tenant,
        tags=["email"],
        custom_data={"source": "email", "message_id": inbound.message_id},
    )
    ticket.save()

    # Initialize SLA deadlines based on priority
    from apps.tickets.services import initialize_sla
    initialize_sla(ticket)

    inbound.ticket = ticket
    inbound.status = InboundEmail.Status.TICKET_CREATED
    inbound.save(update_fields=["ticket", "status", "updated_at"])

    # Auto-assign to an Agent if the tenant has opted in. Run BEFORE
    # the confirmation email fires so the assignee appears on the
    # ticket from the first notification onward.
    _maybe_auto_assign(ticket, tenant)

    # Process attachments
    _attach_inbound_files(inbound, ticket, system_user)

    logger.info(
        "Created ticket #%d from email by %s (tenant: %s)",
        ticket.number,
        inbound.sender_email,
        tenant.slug,
    )

    # Queue confirmation email AFTER transaction commits — but only if
    # the tenant admin has auto-send enabled. When it's off, agents
    # trigger the confirmation manually from the ticket page.
    settings_obj = getattr(tenant, "settings", None)
    if settings_obj is None or settings_obj.auto_send_ticket_created_email:
        def _queue_confirmation():
            try:
                from apps.tickets.tasks import send_ticket_created_email_task

                send_ticket_created_email_task.delay(str(ticket.pk), str(tenant.pk))
            except Exception:
                logger.exception(
                    "Failed to queue ticket created email for ticket #%d",
                    ticket.number,
                )

        transaction.on_commit(_queue_confirmation)
    else:
        logger.info(
            "Auto-send disabled for tenant %s; skipping confirmation email "
            "for ticket #%d (agent can send manually).",
            tenant.slug, ticket.number,
        )
    return ticket


def _maybe_auto_assign(ticket, tenant):
    """
    Apply the tenant's inbound-email auto-assign policy, if enabled.

    Silent no-op when the toggle is off — keeping this outside
    ``_create_ticket_from_email`` means a future change (e.g. a
    per-queue override) only has to touch this helper.

    Failures are swallowed with a log entry: auto-assignment is a
    convenience, not a correctness guarantee. The ticket still exists
    and can be assigned manually.
    """
    settings_obj = getattr(tenant, "settings", None)
    if settings_obj is None or not settings_obj.auto_assign_inbound_email_tickets:
        return

    try:
        from apps.agents.services import auto_assign_email_ticket

        auto_assign_email_ticket(ticket)
    except Exception:
        logger.exception(
            "Auto-assign failed for ticket #%d (tenant %s). "
            "Ticket remains unassigned.",
            ticket.number, tenant.slug,
        )


def _add_reply_to_ticket(inbound, ticket, contact, system_user):
    """Add a comment to an existing ticket from an inbound email reply."""
    body = strip_quoted_reply(inbound.body_text)

    if not body.strip():
        _reject(inbound, "Empty reply body after stripping quotes.")
        return

    ticket_ct = ContentType.objects.get_for_model(Ticket)

    comment = Comment(
        content_type=ticket_ct,
        object_id=ticket.pk,
        author=system_user,
        body=body,
        is_internal=False,
        tenant=ticket.tenant,
    )
    comment.save()

    # Notify the assigned agent about the customer reply.
    # We deliberately avoid firing ticket_comment_created signal here
    # because its handler would queue an outbound email back to the
    # customer who just emailed in, creating an infinite loop.
    if ticket.assignee_id:
        from apps.notifications.models import NotificationType
        from apps.notifications.services import send_notification

        send_notification(
            tenant=ticket.tenant,
            recipient=ticket.assignee,
            notification_type=NotificationType.TICKET_COMMENT,
            title=f"Customer reply on #{ticket.number}",
            body=body[:200],
            data={
                "ticket_id": str(ticket.id),
                "ticket_number": ticket.number,
                "comment_id": str(comment.id),
                "source": "inbound_email",
                "url": f"/tickets/{ticket.number}",
            },
        )

    # Process attachments
    _attach_inbound_files(inbound, ticket, system_user)

    inbound.ticket = ticket
    inbound.status = InboundEmail.Status.REPLY_ADDED
    inbound.save(update_fields=["ticket", "status", "updated_at"])

    # Resume SLA clock if paused (customer replied)
    if inbound.sender_type == InboundEmail.SenderType.CUSTOMER:
        from apps.tickets.signals import _resume_sla_pause
        _resume_sla_pause(ticket, reason="customer_reply")

    # Reopen ticket if it's in "resolved" status (customer reply cancels
    # the auto-close window and returns the ticket to "open").
    # NOTE: resolved→open always targets "open" regardless of pre_wait_status.
    if ticket.status and ticket.status.slug == "resolved":
        _reopen_resolved_ticket(ticket, system_user)

    # Resume from a waiting (pauses_sla) status — restores to the
    # pre-wait status (e.g. In Progress) rather than always going to Open.
    elif ticket.status and getattr(ticket.status, "pauses_sla", False):
        from apps.tickets.services import resume_from_wait
        resume_from_wait(ticket, actor=system_user)

    logger.info(
        "Added reply to ticket #%d from %s",
        ticket.number,
        inbound.sender_email,
    )
    return comment


def _reopen_resolved_ticket(ticket, system_user):
    """
    Reopen a resolved ticket when a customer replies.

    Looks up the 'open' status and transitions via the service layer,
    which handles auto-close task cancellation and field cleanup.
    """
    open_status = TicketStatus.objects.filter(
        slug="open",
    ).first()
    if not open_status:
        logger.warning(
            "Cannot reopen ticket #%d: no 'open' status found for tenant %s.",
            ticket.number,
            ticket.tenant.slug,
        )
        return

    from apps.tickets.services import change_ticket_status

    change_ticket_status(ticket, open_status, actor=system_user)
    logger.info(
        "Ticket #%d reopened from 'resolved' due to customer reply.",
        ticket.number,
    )


# ---------------------------------------------------------------------------
# Attachment processing
# ---------------------------------------------------------------------------


def _attach_inbound_files(inbound, ticket, system_user):
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
    created = 0

    for meta in inbound.attachment_metadata:
        storage_path = meta.get("storage_path")
        if not storage_path or not default_storage.exists(storage_path):
            logger.warning(
                "Inbound attachment file missing: %s (email %s)",
                storage_path,
                inbound.pk,
            )
            continue

        try:
            with default_storage.open(storage_path, "rb") as f:
                django_file = File(f, name=meta.get("filename", "attachment"))
                django_file.size = meta.get("size", 0) or f.size
                create_attachment(tenant, system_user, django_file, ticket)

            default_storage.delete(storage_path)
            created += 1
        except Exception:
            logger.exception(
                "Failed to create attachment from inbound file %s (email %s)",
                meta.get("filename"),
                inbound.pk,
            )

    if created:
        logger.info(
            "Created %d attachment(s) for ticket #%d from inbound email %s",
            created,
            ticket.number,
            inbound.pk,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _handle_bounce(inbound, reason):
    """
    Handle a hard-bounce email: mark as bounced, write a BounceLog,
    try to link to an existing ticket via threading, and flag the
    target Contact as bouncing.

    Runs before tenant/contact resolution, so tenant lookup is
    best-effort for bounce logging.
    """
    inbound.status = InboundEmail.Status.BOUNCED
    inbound.error_message = reason
    inbound.save(update_fields=["status", "error_message", "updated_at"])

    # Best-effort: resolve tenant for the BounceLog FK
    tenant = inbound.tenant
    if not tenant:
        tenant = resolve_tenant_from_address(inbound.recipient_email)
        if tenant:
            inbound.tenant = tenant
            inbound.save(update_fields=["tenant", "updated_at"])

    # Best-effort: link to existing ticket via threading headers
    linked_ticket = None
    if tenant:
        try:
            with tenant_context(tenant):
                linked_ticket = find_existing_ticket(tenant, inbound)
                if linked_ticket:
                    inbound.ticket = linked_ticket
                    inbound.save(update_fields=["ticket", "updated_at"])
        except Exception:
            logger.debug("Could not link bounce to ticket for email %s", inbound.pk)

    # Extract the failed recipient address from X-Failed-Recipients header
    from apps.inbound_email.utils import extract_header
    to_address = ""
    raw = inbound.raw_headers or ""
    if raw:
        to_address = extract_header(raw, "X-Failed-Recipients").strip()
    if not to_address:
        to_address = inbound.recipient_email or ""

    # Write BounceLog
    try:
        BounceLog.objects.create(
            tenant=tenant,
            inbound_email=inbound,
            from_address=inbound.sender_email,
            to_address=to_address,
            subject=inbound.subject or "",
            bounce_reason=reason,
            ticket=linked_ticket,
        )
    except Exception:
        logger.exception("Failed to write BounceLog for email %s", inbound.pk)

    # Flag the Contact as bouncing (if the address matches an existing Contact)
    if tenant and to_address:
        try:
            with tenant_context(tenant):
                Contact.objects.filter(email=to_address).update(email_bouncing=True)
        except Exception:
            logger.exception(
                "Failed to flag contact %s as bouncing", to_address,
            )

    logger.info("Hard bounce recorded for email %s: %s", inbound.pk, reason)


def _reject(inbound, reason):
    """Mark an inbound email as rejected with a reason."""
    inbound.status = InboundEmail.Status.REJECTED
    inbound.error_message = reason
    inbound.save(update_fields=["status", "error_message", "updated_at"])
    logger.info("Rejected inbound email %s: %s", inbound.pk, reason)
