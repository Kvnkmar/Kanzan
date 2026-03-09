"""Add accent_color field to TenantSettings."""

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0002_tenantsettings_business_days_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenantsettings",
            name="accent_color",
            field=models.CharField(
                default="#F59E0B",
                help_text="Hex colour code for accent/highlight elements (badges, alerts).",
                max_length=7,
                validators=[
                    django.core.validators.RegexValidator(
                        message="Enter a valid hex colour code (e.g. #F59E0B).",
                        regex="^#[0-9a-fA-F]{6}$",
                    )
                ],
            ),
        ),
        migrations.AlterField(
            model_name="tenantsettings",
            name="primary_color",
            field=models.CharField(
                default="#6366F1",
                help_text="Hex colour code for the tenant's primary brand colour.",
                max_length=7,
                validators=[
                    django.core.validators.RegexValidator(
                        message="Enter a valid hex colour code (e.g. #6366F1).",
                        regex="^#[0-9a-fA-F]{6}$",
                    )
                ],
            ),
        ),
    ]
