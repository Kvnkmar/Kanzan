"""
Django signals for the custom_fields app.

Connects post_save on Ticket and Contact models to synchronise
CustomFieldValue rows whenever custom_data is updated.
"""

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.contacts.models import Contact
from apps.custom_fields.services import sync_custom_field_values
from apps.tickets.models import Ticket

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Ticket)
def sync_ticket_custom_fields(sender, instance, **kwargs):
    """Sync CustomFieldValue rows when a Ticket is saved."""
    custom_data = getattr(instance, "custom_data", None)
    if custom_data and isinstance(custom_data, dict) and len(custom_data) > 0:
        try:
            sync_custom_field_values(instance, module="ticket")
        except Exception:
            logger.exception(
                "Failed to sync custom fields for Ticket %s.", instance.id
            )


@receiver(post_save, sender=Contact)
def sync_contact_custom_fields(sender, instance, **kwargs):
    """Sync CustomFieldValue rows when a Contact is saved."""
    custom_data = getattr(instance, "custom_data", None)
    if custom_data and isinstance(custom_data, dict) and len(custom_data) > 0:
        try:
            sync_custom_field_values(instance, module="contact")
        except Exception:
            logger.exception(
                "Failed to sync custom fields for Contact %s.", instance.id
            )
