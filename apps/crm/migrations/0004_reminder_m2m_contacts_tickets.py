"""
Convert Reminder.contact / Reminder.ticket from ForeignKey to ManyToManyField.

The new fields are named in the plural — `contacts` and `tickets`. Existing
single-FK values are copied into the new M2M relations so no data is lost.
"""

from django.db import migrations, models


def copy_fk_to_m2m(apps, schema_editor):
    Reminder = apps.get_model("crm", "Reminder")
    for reminder in Reminder.objects.iterator(chunk_size=500):
        if reminder.contact_id:
            reminder.contacts.add(reminder.contact_id)
        if reminder.ticket_id:
            reminder.tickets.add(reminder.ticket_id)


def reverse_copy_m2m_to_fk(apps, schema_editor):
    """Best-effort reverse: keep the first member as the singular FK."""
    Reminder = apps.get_model("crm", "Reminder")
    for reminder in Reminder.objects.iterator(chunk_size=500):
        first_contact = reminder.contacts.order_by("created_at").first()
        first_ticket = reminder.tickets.order_by("created_at").first()
        reminder.contact_id = first_contact.id if first_contact else None
        reminder.ticket_id = first_ticket.id if first_ticket else None
        reminder.save(update_fields=["contact", "ticket"])


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0003_rename_recall_to_reminder"),
    ]

    operations = [
        # 1. Add the new M2M fields alongside the old FKs.
        migrations.AddField(
            model_name="reminder",
            name="contacts",
            field=models.ManyToManyField(
                blank=True,
                related_name="reminders",
                to="contacts.contact",
            ),
        ),
        migrations.AddField(
            model_name="reminder",
            name="tickets",
            field=models.ManyToManyField(
                blank=True,
                related_name="reminders",
                to="tickets.ticket",
            ),
        ),
        # 2. Copy existing FK values into the new M2M tables.
        migrations.RunPython(copy_fk_to_m2m, reverse_copy_m2m_to_fk),
        # 3. Drop the old (tenant, contact) index that referenced the FK column.
        migrations.RemoveIndex(
            model_name="reminder",
            name="crm_reminde_tenant__9a04db_idx",
        ),
        # 4. Drop the now-redundant FK columns. The reverse ops re-create them
        #    so the reverse RunPython above can repopulate.
        migrations.RemoveField(model_name="reminder", name="contact"),
        migrations.RemoveField(model_name="reminder", name="ticket"),
    ]
