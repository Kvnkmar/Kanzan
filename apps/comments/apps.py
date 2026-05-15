from django.apps import AppConfig


class CommentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.comments"
    verbose_name = "Comments"

    def ready(self):
        # Register post_save / post_delete receivers that broadcast
        # comment.created / .updated / .deleted live events. Polymorphic;
        # each event includes the parent's content_type + object_id so
        # listening pages can filter to the entity they care about.
        from apps.comments import signals  # noqa: F401
