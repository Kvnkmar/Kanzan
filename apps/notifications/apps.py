"""
Django AppConfig for the notifications app.

Registers signal handlers on startup so that ticket assignment and
comment-mention events automatically trigger notifications.
"""

from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.notifications"
    verbose_name = "Notifications"

    def ready(self):
        import apps.notifications.signal_handlers  # noqa: F401
