from django.apps import AppConfig


class VoIPConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.voip"
    verbose_name = "VoIP Telephony"

    def ready(self):
        import apps.voip.signals  # noqa: F401
