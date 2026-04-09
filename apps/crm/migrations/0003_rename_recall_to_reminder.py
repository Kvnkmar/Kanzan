"""Rename Recall model to Reminder (data-preserving)."""

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0004_contact_lead_score'),
        ('crm', '0002_add_recall_model'),
        ('tenants', '0006_add_auto_transition_on_assign'),
        ('tickets', '0023_add_inbox_workflow_fields'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # 1. Remove old indexes (they reference old model/table name)
        migrations.RemoveIndex(
            model_name='recall',
            name='recall_overdue_idx',
        ),
        migrations.RemoveIndex(
            model_name='recall',
            name='crm_recall_tenant__4902e6_idx',
        ),
        migrations.RemoveIndex(
            model_name='recall',
            name='crm_recall_tenant__de5c8b_idx',
        ),
        # 2. Rename the model (renames the DB table)
        migrations.RenameModel(
            old_name='Recall',
            new_name='Reminder',
        ),
        # 3. Update related_name on FK fields
        migrations.AlterField(
            model_name='reminder',
            name='assigned_to',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=models.SET_NULL,
                related_name='reminders_assigned',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name='reminder',
            name='contact',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=models.SET_NULL,
                related_name='reminders',
                to='contacts.contact',
            ),
        ),
        migrations.AlterField(
            model_name='reminder',
            name='created_by',
            field=models.ForeignKey(
                on_delete=models.PROTECT,
                related_name='reminders_created',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name='reminder',
            name='ticket',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=models.SET_NULL,
                related_name='reminders',
                to='tickets.ticket',
            ),
        ),
        migrations.AlterField(
            model_name='reminder',
            name='scheduled_at',
            field=models.DateTimeField(
                db_index=True,
                help_text='When this reminder is due.',
            ),
        ),
        # 4. Update Meta options
        migrations.AlterModelOptions(
            name='reminder',
            options={
                'ordering': ['scheduled_at'],
                'verbose_name': 'reminder',
                'verbose_name_plural': 'reminders',
            },
        ),
        # 5. Re-add indexes with new names
        migrations.AddIndex(
            model_name='reminder',
            index=models.Index(
                fields=['tenant', 'scheduled_at', 'completed_at', 'cancelled_at'],
                name='reminder_overdue_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='reminder',
            index=models.Index(
                fields=['tenant', 'assigned_to'],
                name='crm_reminde_tenant__06596b_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='reminder',
            index=models.Index(
                fields=['tenant', 'contact'],
                name='crm_reminde_tenant__9a04db_idx',
            ),
        ),
    ]
