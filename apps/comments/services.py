"""
Service layer for the comments app.

Provides reusable business logic for parsing mentions from comment bodies
and creating activity log entries from anywhere in the codebase.
"""

import logging
import re
import uuid as uuid_mod

from django.contrib.contenttypes.models import ContentType

from apps.comments.models import ActivityLog

logger = logging.getLogger(__name__)

# Matches patterns like @[John Doe](user:550e8400-e29b-41d4-a716-446655440000)
MENTION_PATTERN = re.compile(
    r"@\[(?P<name>[^\]]+)\]\(user:(?P<uuid>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\)",
    re.IGNORECASE,
)


def parse_mentions(body: str) -> list[uuid_mod.UUID]:
    """
    Parse @mention patterns from a comment body and return a deduplicated
    list of user UUIDs.

    Expected format: @[Display Name](user:<uuid>)

    Args:
        body: The comment body text to parse.

    Returns:
        A list of unique user UUIDs found in the body.
    """
    if not body:
        return []

    seen = set()
    user_ids = []

    for match in MENTION_PATTERN.finditer(body):
        raw_uuid = match.group("uuid")
        try:
            user_uuid = uuid_mod.UUID(raw_uuid)
        except ValueError:
            logger.warning("Invalid UUID in mention pattern: %s", raw_uuid)
            continue

        if user_uuid not in seen:
            seen.add(user_uuid)
            user_ids.append(user_uuid)

    return user_ids


def log_activity(
    tenant,
    actor,
    content_object,
    action: str,
    description: str = "",
    changes: dict | None = None,
    request=None,
) -> ActivityLog:
    """
    Create an ActivityLog entry for a given action on a content object.

    This is the primary entry point for audit logging across the platform.
    All model changes, status transitions, comments, imports/exports, etc.
    should be recorded through this function.

    Args:
        tenant: The Tenant instance this activity belongs to.
        actor: The User who performed the action (or None for system actions).
        content_object: The Django model instance the action was performed on.
        action: One of the ActivityLog.Action choices (e.g. "created", "updated").
        description: Optional human-readable description of the action.
        changes: Optional dict of {field_name: [old_value, new_value]} diffs.
        request: Optional HTTP request, used to extract IP address.

    Returns:
        The created ActivityLog instance.
    """
    content_type = ContentType.objects.get_for_model(content_object)

    ip_address = None
    if request is not None:
        ip_address = _get_client_ip(request)

    activity = ActivityLog(
        tenant=tenant,
        content_type=content_type,
        object_id=content_object.pk,
        actor=actor,
        action=action,
        description=description,
        changes=changes or {},
        ip_address=ip_address,
    )
    activity.save()

    logger.info(
        "Activity logged: tenant=%s actor=%s action=%s object=%s:%s",
        tenant.pk,
        actor.pk if actor else "system",
        action,
        content_type.model,
        content_object.pk,
    )

    return activity


def _get_client_ip(request) -> str | None:
    """
    Extract the client IP address from a request object.

    Checks X-Forwarded-For header first (for reverse proxy setups),
    then falls back to REMOTE_ADDR.
    """
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        # X-Forwarded-For can contain multiple IPs; the first is the client.
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")
