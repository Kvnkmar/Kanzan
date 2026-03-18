from django.conf import settings
from django.db import models

from main.models import TenantScopedModel


class NoteColor(models.TextChoices):
    YELLOW = "yellow", "Yellow"
    BLUE = "blue", "Blue"
    GREEN = "green", "Green"
    PINK = "pink", "Pink"
    PURPLE = "purple", "Purple"
    ORANGE = "orange", "Orange"


class QuickNote(TenantScopedModel):
    """Personal sticky note for agents/admins within a tenant."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="quick_notes",
    )
    content = models.TextField(blank=True, default="")
    color = models.CharField(
        max_length=10,
        choices=NoteColor.choices,
        default=NoteColor.YELLOW,
    )
    is_pinned = models.BooleanField(default=False)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-is_pinned", "position", "-updated_at"]
        indexes = [
            models.Index(fields=["tenant", "user", "-is_pinned", "position"]),
        ]

    def __str__(self):
        preview = self.content[:40] + "..." if len(self.content) > 40 else self.content
        return f"Note by {self.user.email}: {preview}"
