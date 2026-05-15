from django.apps import AppConfig


class CrmConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.crm"
    verbose_name = "CRM"

    def ready(self):
        # Register the post_save / post_delete receivers that publish
        # activity.* and reminder.* events onto the live channel.
        from apps.crm import signals  # noqa: F401
