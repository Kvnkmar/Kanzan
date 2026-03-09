"""
Models for the threaded comments, mentions, and activity log system.

Comment supports polymorphic attachment to any model via GenericForeignKey,
threaded replies via self-referential FK, and internal/customer-visible flags.

ActivityLog provides an immutable audit trail of all entity changes across
the platform.
"""

import uuid

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from main.models import TenantScopedModel


class Comment(TenantScopedModel):
    """
    A threaded comment attached to any tenant-scoped entity via GenericForeignKey.

    Comments can be marked as internal (visible only to agents) or
    customer-visible. Threading is achieved through the optional `parent` FK.
    """

    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        related_name="comments",
    )
    object_id = models.UUIDField()
    content_object = GenericForeignKey("content_type", "object_id")

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="comments",
    )
    body = models.TextField()
    is_internal = models.BooleanField(
        default=False,
        help_text="Internal comments are visible only to agents, not customers.",
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="replies",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["content_type", "object_id", "created_at"],
                name="comment_ct_obj_created_idx",
            ),
        ]
        verbose_name = "comment"
        verbose_name_plural = "comments"

    def __str__(self):
        truncated = self.body[:50] + "..." if len(self.body) > 50 else self.body
        return f"Comment by {self.author} on {self.content_type}: {truncated}"

    @property
    def is_reply(self):
        """Return True if this comment is a reply to another comment."""
        return self.parent_id is not None

    def _reply_count_fallback(self):
        """Return the number of direct replies (used when annotation is absent)."""
        return self.replies.count()


class Mention(models.Model):
    """
    Tracks @mentions within comments. Each mention links a comment to the
    mentioned user, enabling notification delivery and mention highlighting.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    comment = models.ForeignKey(
        Comment,
        on_delete=models.CASCADE,
        related_name="mentions",
    )
    mentioned_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="mentions_received",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        unique_together = [("comment", "mentioned_user")]
        verbose_name = "mention"
        verbose_name_plural = "mentions"

    def __str__(self):
        return f"@{self.mentioned_user} in comment {self.comment_id}"


class ActivityLog(TenantScopedModel):
    """
    Immutable audit trail for all entity changes within a tenant.

    Stores the actor, action type, human-readable description, and a
    structured JSON diff of changed fields ({field: [old_value, new_value]}).
    """

    class Action(models.TextChoices):
        CREATED = "created", "Created"
        UPDATED = "updated", "Updated"
        ASSIGNED = "assigned", "Assigned"
        STATUS_CHANGED = "status_changed", "Status Changed"
        COMMENTED = "commented", "Commented"
        DELETED = "deleted", "Deleted"
        FIELD_CHANGED = "field_changed", "Field Changed"
        IMPORTED = "imported", "Imported"
        EXPORTED = "exported", "Exported"
        CLOSED = "closed", "Closed"
        REOPENED = "reopened", "Reopened"
        ATTACHMENT_ADDED = "attachment_added", "Attachment Added"
        ATTACHMENT_REMOVED = "attachment_removed", "Attachment Removed"

    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        related_name="activity_logs",
    )
    object_id = models.UUIDField()
    content_object = GenericForeignKey("content_type", "object_id")

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activity_logs",
    )
    action = models.CharField(
        max_length=50,
        choices=Action.choices,
    )
    description = models.TextField(blank=True, default="")
    changes = models.JSONField(
        default=dict,
        blank=True,
        help_text="Structured diff: {field_name: [old_value, new_value]}",
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["tenant", "content_type", "object_id", "created_at"],
                name="actlog_tenant_ct_obj_created",
            ),
            models.Index(
                fields=["tenant", "actor", "created_at"],
                name="actlog_tenant_actor_created",
            ),
        ]
        verbose_name = "activity log"
        verbose_name_plural = "activity logs"

    def __str__(self):
        actor_str = self.actor or "System"
        return f"{actor_str} {self.action} {self.content_type} {self.object_id}"
