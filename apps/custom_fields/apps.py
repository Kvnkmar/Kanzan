from django.apps import AppConfig


class CustomFieldsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.custom_fields"
    verbose_name = "Custom Fields"

    def ready(self):
        import apps.custom_fields.signals  # noqa: F401
