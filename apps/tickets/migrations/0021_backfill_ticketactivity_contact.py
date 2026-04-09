"""
Data migration: backfill TicketActivity.contact from ticket.contact.

Reversible — the reverse simply nulls the field back out.
"""

from django.db import migrations
from django.db.models import OuterRef, Subquery


def backfill_contact(apps, schema_editor):
    """Set TicketActivity.contact = ticket.contact for all existing rows."""
    TicketActivity = apps.get_model("tickets", "TicketActivity")
    Ticket = apps.get_model("tickets", "Ticket")
    TicketActivity.objects.filter(
        contact__isnull=True,
        ticket__contact__isnull=False,
    ).update(
        contact=Subquery(
            Ticket.objects.filter(pk=OuterRef("ticket_id")).values("contact_id")[:1]
        )
    )


def reverse_backfill(apps, schema_editor):
    """Null out the contact FK (reversible)."""
    TicketActivity = apps.get_model("tickets", "TicketActivity")
    TicketActivity.objects.filter(contact__isnull=False).update(contact=None)


class Migration(migrations.Migration):

    dependencies = [
        ("tickets", "0020_add_contact_to_ticketactivity"),
    ]

    operations = [
        migrations.RunPython(backfill_contact, reverse_backfill),
    ]
