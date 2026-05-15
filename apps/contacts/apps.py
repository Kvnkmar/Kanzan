from django.apps import AppConfig


class ContactsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.contacts"
    verbose_name = "Contacts"

    def ready(self):
        # Register post_save / post_delete receivers that broadcast
        # contact.*, company.*, account.*, and contact_group.* live events.
        from apps.contacts import signals  # noqa: F401
