"""Signal handlers that broadcast news-feed changes onto the live channel.

These signals power the "real-time news feed" behaviour: when an admin
publishes an announcement, every connected client in the tenant sees it
appear without reloading, and reactions update in place.

The handlers do *minimal* work in-band; the heavy lifting (full payload
serialisation, channel-layer send) is deferred to ``on_commit`` via the
``broadcast_live_event`` helper, so a rolled-back create never leaks an
event.
"""

from __future__ import annotations

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.newsfeed.models import NewsPost, NewsPostReaction
from apps.tenants.live import broadcast_live_event


def _serialise_post(post: NewsPost) -> dict:
    """Compact payload suitable for client-side prepend / update."""
    return {
        "id":           str(post.id),
        "title":        post.title,
        "category":     post.category,
        "is_pinned":    post.is_pinned,
        "is_published": post.is_published,
        "is_urgent":    post.is_urgent,
        "emoji":        post.emoji,
        "author_id":    str(post.author_id) if post.author_id else None,
        "created_at":   post.created_at.isoformat() if post.created_at else None,
        "updated_at":   post.updated_at.isoformat() if post.updated_at else None,
    }


@receiver(post_save, sender=NewsPost)
def broadcast_newspost_save(sender, instance, created, **kwargs):
    """Broadcast a newsfeed.created or newsfeed.updated event."""
    event = "newsfeed.created" if created else "newsfeed.updated"
    broadcast_live_event(
        tenant=instance.tenant,
        event=event,
        payload=_serialise_post(instance),
    )


@receiver(post_delete, sender=NewsPost)
def broadcast_newspost_delete(sender, instance, **kwargs):
    broadcast_live_event(
        tenant=instance.tenant,
        event="newsfeed.deleted",
        payload={"id": str(instance.id)},
    )


@receiver(post_save, sender=NewsPostReaction)
def broadcast_reaction_save(sender, instance, created, **kwargs):
    """Broadcast a newsfeed.reacted event so reaction counters update live."""
    broadcast_live_event(
        tenant=instance.tenant,
        event="newsfeed.reacted",
        payload={
            "post_id":  str(instance.post_id),
            "user_id":  str(instance.user_id),
            "reaction": instance.reaction,
            "added":    created,
        },
    )


@receiver(post_delete, sender=NewsPostReaction)
def broadcast_reaction_delete(sender, instance, **kwargs):
    broadcast_live_event(
        tenant=instance.tenant,
        event="newsfeed.reacted",
        payload={
            "post_id":  str(instance.post_id),
            "user_id":  str(instance.user_id),
            "reaction": instance.reaction,
            "added":    False,
        },
    )
