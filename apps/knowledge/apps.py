from django.apps import AppConfig


class KnowledgeConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.knowledge"
    verbose_name = "Knowledge Base"

    def ready(self):
        import apps.knowledge.signals  # noqa: F401
