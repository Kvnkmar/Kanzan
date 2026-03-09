"""
Mention parsing and notification utilities for the messaging app.

Mentions follow the format ``@[Display Name](user:<uuid>)`` which allows
the frontend to render rich mention chips while keeping the raw text
searchable.

Also provides ``notify_new_message`` which sends in-app notifications
to all conversation participants (except the author and muted users)
whenever a new message is created.
"""

import logging
import re

MENTION_REGEX = r"@\[([^\]]+)\]\(user:([a-f0-9-]+)\)"

_mention_pattern = re.compile(MENTION_REGEX)

logger = logging.getLogger(__name__)


def parse_mentions(body: str) -> list[str]:
    """
    Extract user UUIDs from mention markup in a message body.

    Args:
        body: The raw message text potentially containing mention markup.

    Returns:
        A deduplicated list of user UUID strings found in the body.

    Example::

        >>> parse_mentions("Hey @[Alice](user:abc-123) and @[Bob](user:def-456)")
        ["abc-123", "def-456"]
    """
    if not body:
        return []
    matches = _mention_pattern.findall(body)
    # Deduplicate while preserving order
    seen: set[str] = set()
    user_ids: list[str] = []
    for _display_name, user_id in matches:
        if user_id not in seen:
            seen.add(user_id)
            user_ids.append(user_id)
    return user_ids


def notify_mentions(message, tenant) -> None:
    """
    Send notifications to all users mentioned in a message.

    Parses the message body for mention markup, resolves valid user UUIDs
    within the tenant, and dispatches a notification for each mentioned user
    (excluding the message author to avoid self-notifications).

    This function is designed to be called synchronously from within a
    consumer or view. For high-throughput scenarios, consider dispatching
    to a Celery task instead.

    Args:
        message: A ``Message`` model instance whose body will be parsed.
        tenant: The ``Tenant`` instance used to scope notification delivery.
    """
    from apps.accounts.models import User

    user_ids = parse_mentions(message.body)
    if not user_ids:
        return

    # Filter to users who actually exist -- silently ignore invalid UUIDs
    mentioned_users = User.objects.filter(id__in=user_ids)

    for user in mentioned_users:
        # Don't notify the author about their own mentions
        if message.author_id and user.pk == message.author_id:
            continue

        try:
            _send_mention_notification(
                user=user,
                message=message,
                tenant=tenant,
            )
        except Exception:
            logger.exception(
                "Failed to send mention notification to user %s for message %s",
                user.pk,
                message.pk,
            )


def _send_mention_notification(user, message, tenant) -> None:
    """
    Dispatch a single mention notification via the notifications app.

    This is an internal helper that isolates the integration point with the
    notification service so the dependency can be swapped or mocked easily.
    """
    try:
        from apps.notifications.services import send_notification

        author_name = ""
        if message.author:
            author_name = message.author.get_full_name() or str(message.author)

        send_notification(
            recipient=user,
            tenant=tenant,
            notification_type="mention",
            title="You were mentioned in a conversation",
            body=f"{author_name} mentioned you: {message.body[:200]}",
            data={
                "conversation_id": str(message.conversation_id),
                "message_id": str(message.pk),
            },
        )
    except ImportError:
        # Notifications app may not have the service module yet -- log and
        # degrade gracefully.
        logger.warning(
            "notifications.services.send_notification is not available; "
            "mention notification for user %s skipped.",
            user.pk,
        )
    except Exception:
        raise


def notify_new_message(message, tenant) -> None:
    """
    Send notifications to all conversation participants for a new message.

    Notifies every participant in the conversation except:
    - The message author (no self-notification).
    - Users who have muted the conversation.
    - Users who were already notified via mention (handled by ``notify_mentions``).

    Args:
        message: A ``Message`` model instance (must have ``conversation`` and
                 ``author`` loaded).
        tenant: The ``Tenant`` instance used to scope notification delivery.
    """
    from apps.messaging.models import ConversationParticipant

    # Get all non-muted participants except the author
    participants = (
        ConversationParticipant.objects.filter(
            conversation_id=message.conversation_id,
            is_muted=False,
        )
        .exclude(user_id=message.author_id)
        .select_related("user")
    )

    # Build set of already-mentioned user IDs so we don't double-notify
    mentioned_ids = set()
    mention_uuids = parse_mentions(message.body)
    if mention_uuids:
        mentioned_ids = set(mention_uuids)

    for participant in participants:
        # Skip if already notified via mention
        if str(participant.user_id) in mentioned_ids:
            continue

        try:
            _send_message_notification(
                user=participant.user,
                message=message,
                tenant=tenant,
            )
        except Exception:
            logger.exception(
                "Failed to send message notification to user %s for message %s",
                participant.user_id,
                message.pk,
            )


def _send_message_notification(user, message, tenant) -> None:
    """
    Dispatch a single new-message notification via the notifications app.
    """
    try:
        from apps.notifications.services import send_notification

        author_name = ""
        if message.author:
            author_name = message.author.get_full_name() or str(message.author)

        # Build a conversation display name
        conversation = message.conversation
        conv_name = conversation.name or "a conversation"
        if conversation.type == "direct":
            conv_name = "a direct message"

        preview = message.body[:120]
        if len(message.body) > 120:
            preview += "..."

        send_notification(
            recipient=user,
            tenant=tenant,
            notification_type="message",
            title=f"New message from {author_name}",
            body=preview,
            data={
                "conversation_id": str(message.conversation_id),
                "message_id": str(message.pk),
                "author_name": author_name,
            },
        )
    except ImportError:
        logger.warning(
            "notifications.services.send_notification is not available; "
            "message notification for user %s skipped.",
            user.pk,
        )
    except Exception:
        raise
