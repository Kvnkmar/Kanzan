"""
Signal handlers that bridge ticket/comment events to the notification system.

Connects to:
- ``ticket_assigned`` -- a custom signal dispatched when a ticket is assigned
  to a user, sending a notification to the assignee.
- ``ticket_comment_created`` -- a custom signal dispatched when a comment is
  created on a ticket, sending mention notifications to referenced users.

Custom signals are defined in this module so that other apps can import and
fire them without depending on the notifications app's internals.
"""

import logging
import re

from django.dispatch import Signal, receiver

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom signals
# ---------------------------------------------------------------------------

# Fired by the comments/tickets app when a new comment is created.
# Sender: the Comment model class.
# Provides: instance (Comment), tenant, ticket, author.
ticket_comment_created = Signal()

# Import ticket_assigned from the tickets app so the handler listens to the
# same Signal instance that fire_ticket_assigned_signal() sends on.
from apps.tickets.signals import ticket_assigned  # noqa: E402


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

# Pattern to extract @mentions from comment bodies.
# Matches ``@user@example.com`` style references.
_MENTION_RE = re.compile(r"@([\w.+-]+@[\w.-]+\.\w+)")


@receiver(ticket_assigned)
def handle_ticket_assigned(sender, instance, tenant, assignee, assigned_by=None, **kwargs):
    """
    Send a notification to the assignee when a ticket is assigned to them.

    Silently skips if the assignee is the same user who performed the
    assignment (no self-notification).
    """
    if assigned_by and assignee.id == assigned_by.id:
        logger.debug(
            "Skipping self-assignment notification for ticket #%s.",
            instance.number,
        )
        return

    try:
        from apps.notifications.models import NotificationType
        from apps.notifications.services import send_notification

        assigned_by_name = ""
        if assigned_by:
            assigned_by_name = assigned_by.get_full_name() or assigned_by.email

        if assigned_by_name:
            title = f"{assigned_by_name} assigned you to #{instance.number}"
        else:
            title = f"You've been assigned to ticket #{instance.number}"
        body = f"{instance.subject}"

        send_notification(
            tenant=tenant,
            recipient=assignee,
            notification_type=NotificationType.TICKET_ASSIGNED,
            title=title,
            body=body,
            data={
                "ticket_id": str(instance.id),
                "ticket_number": instance.number,
                "url": f"/tickets/{instance.number}",
            },
        )
    except Exception:
        logger.exception(
            "Failed to send ticket_assigned notification for ticket #%s.",
            instance.number,
        )


@receiver(ticket_comment_created)
def handle_comment_notification(sender, instance, tenant, ticket, author, **kwargs):
    """
    When a comment is created on a ticket:

    1. Notify the ticket assignee (if different from the comment author).
    2. Notify ``@email`` mentioned users.

    Internal comments only notify tenant members.  All recipients are
    deduplicated so nobody receives more than one notification.
    """
    try:
        from apps.notifications.models import NotificationType
        from apps.notifications.services import send_notification

        author_name = author.get_full_name() or author.email
        body_text = getattr(instance, "body", "") or getattr(instance, "content", "") or ""
        is_internal = getattr(instance, "is_internal", False)
        comment_label = "internal note" if is_internal else "comment"

        notified_ids = {author.id}  # never notify the author themselves

        # -- 1) Notify ticket assignee --
        if ticket.assignee_id and ticket.assignee_id not in notified_ids:
            send_notification(
                tenant=tenant,
                recipient=ticket.assignee,
                notification_type=NotificationType.TICKET_COMMENT,
                title=f"{author_name} added a {comment_label} on #{ticket.number}",
                body=body_text[:200] if body_text else ticket.subject,
                data={
                    "ticket_id": str(ticket.id),
                    "ticket_number": ticket.number,
                    "comment_id": str(instance.id),
                    "url": f"/tickets/{ticket.number}",
                },
            )
            notified_ids.add(ticket.assignee_id)

        # -- 2) Notify @mentioned users --
        if body_text:
            emails = set(_MENTION_RE.findall(body_text))
            if emails:
                from django.contrib.auth import get_user_model

                User = get_user_model()
                mentioned_users = User.objects.filter(
                    email__in=emails,
                    memberships__tenant=tenant,
                    memberships__is_active=True,
                ).distinct()

                for user in mentioned_users:
                    if user.id in notified_ids:
                        continue
                    send_notification(
                        tenant=tenant,
                        recipient=user,
                        notification_type=NotificationType.MENTION,
                        title=f"{author_name} mentioned you on #{ticket.number}",
                        body=body_text[:200],
                        data={
                            "ticket_id": str(ticket.id),
                            "ticket_number": ticket.number,
                            "comment_id": str(instance.id),
                            "url": f"/tickets/{ticket.number}",
                        },
                    )
                    notified_ids.add(user.id)

        logger.info(
            "Sent %d comment notification(s) for comment %s on ticket #%s.",
            len(notified_ids) - 1,  # subtract author
            instance.id,
            ticket.number,
        )

        # -- 3) Email the ticket's contact (customer) for public replies --
        if not is_internal and ticket.contact_id:
            _queue_contact_reply_email(ticket, instance, author)

    except Exception:
        logger.exception(
            "Failed to process comment notifications for comment %s.",
            instance.id,
        )


def _queue_contact_reply_email(ticket, comment, author):
    """
    Queue an outbound email to the ticket's contact for a public agent reply.

    Uses transaction.on_commit() to ensure the Celery task is only
    dispatched after the comment is committed to the database. This
    prevents the task from running against rolled-back data.

    Skips if the comment author is the contact themselves (e.g. inbound
    email replies already processed by the system).
    """
    try:
        contact = ticket.contact
        if not contact or not contact.email:
            return

        # Don't email the contact about their own replies
        if contact.email == author.email:
            return

        agent_name = author.get_full_name() or author.email
        comment_body = getattr(comment, "body", "") or ""

        # Capture values for the closure (avoid referencing Django model
        # instances inside on_commit which may be stale)
        ticket_pk = str(ticket.pk)
        tenant_id = str(ticket.tenant_id)

        from django.db import transaction

        def _dispatch():
            from apps.tickets.tasks import send_ticket_reply_email_task

            send_ticket_reply_email_task.delay(
                ticket_pk,
                comment_body,
                agent_name,
                tenant_id,
            )
            logger.debug(
                "Queued contact reply email for ticket #%d to %s",
                ticket.number,
                contact.email,
            )

        transaction.on_commit(_dispatch)
    except Exception:
        logger.exception(
            "Failed to queue contact reply email for ticket #%d",
            ticket.number,
        )
