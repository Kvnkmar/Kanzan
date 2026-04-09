"""Signals for the Knowledge Base app."""

from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.knowledge.models import Article


@receiver(post_save, sender=Article)
def update_search_vector(sender, instance, **kwargs):
    """Keep the search_vector field current after every save.

    Uses .update() instead of .save() to avoid signal recursion.
    Skips on non-PostgreSQL backends (SearchVector requires to_tsvector).
    """
    db_engine = settings.DATABASES["default"]["ENGINE"]
    if "postgresql" not in db_engine:
        return

    from django.contrib.postgres.search import SearchVector

    Article.objects.filter(pk=instance.pk).update(
        search_vector=(
            SearchVector("title", weight="A")
            + SearchVector("content", weight="B")
        )
    )
