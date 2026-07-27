# Generated for CRM — personal boards (visible only to creator).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('kanban', '0003_alter_cardposition_tenant'),
    ]

    operations = [
        migrations.AddField(
            model_name='board',
            name='is_personal',
            field=models.BooleanField(
                default=False,
                help_text=(
                    'Personal boards are visible only to their creator and do '
                    'not sync card movements back to ticket status.'
                ),
            ),
        ),
    ]
