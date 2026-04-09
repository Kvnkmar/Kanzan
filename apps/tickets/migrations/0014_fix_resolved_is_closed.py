"""
Data migration: set is_closed=False on all TicketStatus rows with slug='resolved'.

The "Resolved" status was originally seeded with is_closed=True, but Phase 4
requires it to be a non-closed holding state so that the auto-close timer can
run before the ticket moves to the terminal "Closed" status.
"""

from django.db import migrations


def fix_resolved_is_closed(apps, schema_editor):
    TicketStatus = apps.get_model("tickets", "TicketStatus")
    updated = TicketStatus.objects.filter(slug="resolved", is_closed=True).update(
        is_closed=False,
    )
    if updated:
        print(f"  Updated {updated} 'resolved' status(es) to is_closed=False")


def reverse_fix(apps, schema_editor):
    TicketStatus = apps.get_model("tickets", "TicketStatus")
    TicketStatus.objects.filter(slug="resolved", is_closed=False).update(
        is_closed=True,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("tickets", "0013_phase4_closure_fields"),
    ]

    operations = [
        migrations.RunPython(fix_resolved_is_closed, reverse_fix),
    ]
