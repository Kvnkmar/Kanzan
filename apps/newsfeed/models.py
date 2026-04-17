import uuid

from django.conf import settings
from django.db import models

from main.models import TenantScopedModel


class PostCategory(models.TextChoices):
    ANNOUNCEMENT = "announcement", "Announcement"
    UPDATE = "update", "Update"
    CELEBRATION = "celebration", "Celebration"
    INCIDENT = "incident", "Incident"
    GENERAL = "general", "General"


class ReactionType(models.TextChoices):
    THUMBS_UP = "thumbs_up", "👍"
    CELEBRATION = "celebration", "🎉"
    HEART = "heart", "❤️"
    ROCKET = "rocket", "🚀"
    EYES = "eyes", "👀"
    HUNDRED = "hundred", "💯"


class NewsPost(TenantScopedModel):
    """Internal news/announcement post managed by admins."""

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="news_posts",
    )
    title = models.CharField(max_length=200)
    content = models.TextField()
    category = models.CharField(
        max_length=20,
        choices=PostCategory.choices,
        default=PostCategory.GENERAL,
    )
    is_pinned = models.BooleanField(default=False)
    is_published = models.BooleanField(default=True)
    is_urgent = models.BooleanField(
        default=False,
        help_text="Highlight as urgent/breaking news.",
    )
    emoji = models.CharField(
        max_length=10,
        blank=True,
        default="",
        help_text="Custom emoji icon for the post.",
    )
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Auto-hide after this time.",
    )

    class Meta:
        ordering = ["-is_pinned", "-created_at"]
        indexes = [
            models.Index(fields=["tenant", "-is_pinned", "-created_at"]),
        ]

    def __str__(self):
        return self.title


class NewsPostReaction(TenantScopedModel):
    """Emoji reaction on a news post. One reaction per user per post."""

    post = models.ForeignKey(
        NewsPost,
        on_delete=models.CASCADE,
        related_name="reactions",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="news_reactions",
    )
    reaction = models.CharField(
        max_length=20,
        choices=ReactionType.choices,
    )

    class Meta:
        unique_together = [("post", "user")]
        indexes = [
            models.Index(fields=["post", "reaction"]),
        ]

    def __str__(self):
        return f"{self.user} reacted {self.reaction} on {self.post}"


class NewsPostRead(models.Model):
    """Tracks which posts a user has read. Row presence = read."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    post = models.ForeignKey(
        NewsPost,
        on_delete=models.CASCADE,
        related_name="reads",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="news_reads",
    )
    read_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("post", "user")]

    def __str__(self):
        return f"{self.user} read {self.post}"
