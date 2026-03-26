"""
Seed TicketCounter rows for all existing tenants based on their current
maximum ticket number. This ensures the atomic counter starts at the right
value after migration.
"""

from django.db import migrations


def seed_counters(apps, schema_editor):
    Ticket = apps.get_model("tickets", "Ticket")
    TicketCounter = apps.get_model("tickets", "TicketCounter")
    Tenant = apps.get_model("tenants", "Tenant")

    for tenant in Tenant.objects.all():
        max_number = (
            Ticket.objects.filter(tenant=tenant)
            .order_by("-number")
            .values_list("number", flat=True)
            .first()
        ) or 0
        TicketCounter.objects.get_or_create(
            tenant=tenant,
            defaults={"last_number": max_number},
        )


def reverse_seed(apps, schema_editor):
    TicketCounter = apps.get_model("tickets", "TicketCounter")
    TicketCounter.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("tickets", "0009_add_ticket_counter"),
    ]

    operations = [
        migrations.RunPython(seed_counters, reverse_seed),
    ]
