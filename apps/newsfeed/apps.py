from django.apps import AppConfig


class NewsfeedConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.newsfeed"
    verbose_name = "News Feed"

    def ready(self):
        # Import for the side effect of registering the post_save /
        # post_delete receivers that broadcast onto the live channel.
        from apps.newsfeed import signals  # noqa: F401
