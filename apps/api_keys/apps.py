from django.apps import AppConfig


class ApiKeysConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.api_keys"
    verbose_name = "API Keys"

    def ready(self):
        # Register drf-spectacular OpenAPI extension for APIKeyAuthentication
        from . import extensions  # noqa: F401
