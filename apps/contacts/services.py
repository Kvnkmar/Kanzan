"""
Service-layer functions for contact operations.

Provides the unified contact event logging that powers the 360° timeline.
"""

import logging

from django.utils import timezone

logger = logging.getLogger(__name__)


def log_contact_event(contact, event_type, description, source, actor=None, metadata=None):
    """
    Create a ContactEvent and update Contact.last_activity_at.

    Args:
        contact: The Contact instance.
        event_type: Short event label (e.g. "created", "assigned", "status_changed").
        description: Human-readable description of what happened.
        source: One of ContactEvent.Source choices ("ticket", "activity", "email", "manual").
        actor: Optional User who triggered the event.
        metadata: Optional dict of structured event data.
    """
    from apps.contacts.models import ContactEvent

    if metadata is None:
        metadata = {}

    ContactEvent.objects.create(
        tenant=contact.tenant,
        contact=contact,
        event_type=event_type,
        description=description,
        source=source,
        actor=actor,
        metadata=metadata,
    )

    # Update last_activity_at on the contact (single-field update to avoid races)
    from apps.contacts.models import Contact

    Contact.unscoped.filter(pk=contact.pk).update(last_activity_at=timezone.now())
