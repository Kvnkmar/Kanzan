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
def handle_comment_mention(sender, instance, tenant, ticket, author, **kwargs):
    """
    Parse ``@email`` mentions from a comment body and notify each
    mentioned user.

    Only notifies users who are members of the tenant to prevent
    information leakage.
    """
    body_text = getattr(instance, "body", "") or getattr(instance, "content", "")
    if not body_text:
        return

    emails = set(_MENTION_RE.findall(body_text))
    if not emails:
        return

    try:
        from django.contrib.auth import get_user_model

        from apps.notifications.models import NotificationType
        from apps.notifications.services import send_notification

        User = get_user_model()

        # Only notify users who actually belong to this tenant.
        mentioned_users = User.objects.filter(
            email__in=emails,
            memberships__tenant=tenant,
            memberships__is_active=True,
        ).distinct()

        author_name = author.get_full_name() or author.email

        for user in mentioned_users:
            # Do not notify the comment author about their own mention.
            if user.id == author.id:
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

        logger.info(
            "Processed %d mention(s) from comment %s on ticket #%s.",
            mentioned_users.count(),
            instance.id,
            ticket.number,
        )
    except Exception:
        logger.exception(
            "Failed to process mention notifications for comment %s.",
            instance.id,
        )
