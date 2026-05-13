# Generated for phone field widening to match form input (maxlength=30).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0004_contact_lead_score'),
    ]

    operations = [
        migrations.AlterField(
            model_name='contact',
            name='phone',
            field=models.CharField(blank=True, max_length=30, null=True),
        ),
        migrations.AlterField(
            model_name='company',
            name='phone',
            field=models.CharField(blank=True, max_length=30, null=True),
        ),
    ]
