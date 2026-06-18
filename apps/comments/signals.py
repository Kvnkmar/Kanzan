"""Signal handlers that broadcast polymorphic Comment changes live.

Comments are attached to any tenant-scoped entity via GenericForeignKey
(tickets, contacts, knowledge articles, etc.).  The payload includes the
parent's ``content_type`` (``"app_label.model"``) and ``object_id`` so
each page can filter for comments on the entity it cares about without a
parent-specific channel.

Only public-facing comments are broadcast.  Internal comments are
restricted to agents; receiving clients that lack agent-level scope
should filter ``is_internal`` themselves (or we can layer a per-role
group later if leakage becomes an issue).
"""

from __future__ import annotations

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.comments.models import Comment
from apps.tenants.live import broadcast_live_event


def _content_type_label(c: Comment) -> str:
    ct = c.content_type
    if not ct:
        return ""
    return f"{ct.app_label}.{ct.model}"


def _serialise_comment(c: Comment) -> dict:
    # The live bus fans to EVERY tenant member (incl. Viewers), but internal
    # comments are agent-only (enforced server-side in CommentViewSet). Never
    # put an internal body on the wire -- broadcast only the change signal and
    # let agent clients refetch the full note via the access-gated HTTP API.
    is_internal = c.is_internal
    return {
        "id":            str(c.id),
        "body":          None if is_internal else c.body,
        "is_internal":   is_internal,
        "author_id":     str(c.author_id) if c.author_id else None,
        "parent_id":     str(c.parent_id) if c.parent_id else None,
        "content_type":  _content_type_label(c),
        "object_id":     str(c.object_id) if c.object_id else None,
        "created_at":    c.created_at.isoformat() if c.created_at else None,
        "updated_at":    c.updated_at.isoformat() if c.updated_at else None,
    }


@receiver(post_save, sender=Comment)
def broadcast_comment_save(sender, instance, created, **kwargs):
    broadcast_live_event(
        tenant=instance.tenant,
        event="comment.created" if created else "comment.updated",
        payload=_serialise_comment(instance),
    )


@receiver(post_delete, sender=Comment)
def broadcast_comment_delete(sender, instance, **kwargs):
    broadcast_live_event(
        tenant=instance.tenant,
        event="comment.deleted",
        payload={
            "id":           str(instance.id),
            "content_type": _content_type_label(instance),
            "object_id":    str(instance.object_id) if instance.object_id else None,
            "parent_id":    str(instance.parent_id) if instance.parent_id else None,
        },
    )
