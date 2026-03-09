"""
Notification service layer.

Provides the ``send_notification`` function as the single entry point for
creating and dispatching notifications across the platform. Signal handlers,
views, and Celery tasks should all use this function rather than
manipulating models directly.
"""

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from apps.notifications.models import Notification, NotificationPreference

logger = logging.getLogger(__name__)


def send_notification(
    tenant,
    recipient,
    notification_type,
    title,
    body="",
    data=None,
):
    """
    Create a notification and dispatch it through the configured channels.

    Steps:
        1. Persist a ``Notification`` record.
        2. Look up the recipient's ``NotificationPreference`` for this type
           (defaults to in_app=True, email=True when no preference exists).
        3. If ``in_app`` is enabled, push the notification to the user's
           WebSocket group via the Django Channels layer.
        4. If ``email`` is enabled, enqueue the ``send_notification_email``
           Celery task.

    Args:
        tenant: The ``Tenant`` instance the notification belongs to.
        recipient: The ``User`` instance who should receive the notification.
        notification_type: One of ``NotificationType`` values (str).
        title: Short human-readable summary.
        body: Optional longer description.
        data: Optional dict with contextual data (e.g. ticket_id, url).

    Returns:
        The created ``Notification`` instance.
    """
    if data is None:
        data = {}

    # 1. Persist the notification ----------------------------------------
    notification = Notification(
        tenant=tenant,
        recipient=recipient,
        type=notification_type,
        title=title,
        body=body,
        data=data,
    )
    # Bypass TenantAwareManager auto-scoping by setting tenant explicitly
    # and calling save directly.
    notification.save()

    logger.info(
        "Notification created: id=%s type=%s recipient=%s tenant=%s",
        notification.id,
        notification_type,
        recipient.id,
        tenant.id,
    )

    # 2. Resolve delivery preferences ------------------------------------
    try:
        pref = NotificationPreference.unscoped.get(
            user=recipient,
            tenant=tenant,
            notification_type=notification_type,
        )
        deliver_in_app = pref.in_app
        deliver_email = pref.email
    except NotificationPreference.DoesNotExist:
        # Default: both channels enabled.
        deliver_in_app = True
        deliver_email = True

    # 3. In-app push via Channels ----------------------------------------
    if deliver_in_app:
        _push_to_websocket(notification)

    # 4. Email via Celery ------------------------------------------------
    if deliver_email:
        _queue_email(notification)

    return notification


def _push_to_websocket(notification):
    """
    Send a notification payload to the user's WebSocket group.

    Uses ``async_to_sync`` so this can be called from synchronous code
    (e.g. signal handlers, service functions).
    """
    channel_layer = get_channel_layer()
    if channel_layer is None:
        logger.warning(
            "Channel layer not configured; skipping WebSocket push for "
            "notification %s.",
            notification.id,
        )
        return

    group_name = f"notifications_{notification.recipient_id}"
    payload = {
        "id": str(notification.id),
        "type": notification.type,
        "title": notification.title,
        "body": notification.body,
        "data": notification.data,
        "is_read": notification.is_read,
        "created_at": notification.created_at.isoformat(),
    }

    try:
        async_to_sync(channel_layer.group_send)(
            group_name,
            {
                "type": "notification.send",
                "payload": payload,
            },
        )
        logger.debug(
            "Pushed notification %s to group %s.",
            notification.id,
            group_name,
        )
    except Exception:
        logger.exception(
            "Failed to push notification %s to WebSocket group %s.",
            notification.id,
            group_name,
        )


def _queue_email(notification):
    """
    Enqueue the ``send_notification_email`` Celery task.

    Import is deferred to avoid circular imports between services and tasks.
    """
    try:
        from apps.notifications.tasks import send_notification_email

        send_notification_email.delay(str(notification.id))
        logger.debug(
            "Queued email task for notification %s.",
            notification.id,
        )
    except Exception:
        logger.exception(
            "Failed to queue email task for notification %s.",
            notification.id,
        )
