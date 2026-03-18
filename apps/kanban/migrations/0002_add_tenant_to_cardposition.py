"""
Add tenant scoping to CardPosition.

Previously CardPosition inherited from models.Model, bypassing the
TenantScopedModel base class that Board and Column use.  This meant
CardPosition rows had no tenant FK and were invisible to the
TenantAwareManager, creating a cross-tenant data isolation gap.

This migration:
1. Adds tenant (nullable), created_at, updated_at fields.
2. Back-fills tenant from column → board → tenant for existing rows.
3. Makes tenant non-nullable.
"""

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


def populate_cardposition_tenant(apps, schema_editor):
    """Set tenant on every CardPosition from its column's board."""
    CardPosition = apps.get_model("kanban", "CardPosition")
    for card in CardPosition.objects.select_related("column__board").all():
        card.tenant_id = card.column.board.tenant_id
        card.save(update_fields=["tenant_id"])


class Migration(migrations.Migration):

    dependencies = [
        ("kanban", "0001_initial"),
        ("tenants", "0001_initial"),
    ]

    operations = [
        # Step 1: Add tenant as nullable + timestamp fields
        migrations.AddField(
            model_name="cardposition",
            name="tenant",
            field=models.ForeignKey(
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="kanban_cardposition_set",
                to="tenants.tenant",
            ),
        ),
        migrations.AddField(
            model_name="cardposition",
            name="created_at",
            field=models.DateTimeField(
                auto_now_add=True,
                default=django.utils.timezone.now,
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="cardposition",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
        # Step 2: Back-fill tenant from column.board.tenant
        migrations.RunPython(
            populate_cardposition_tenant,
            reverse_code=migrations.RunPython.noop,
        ),
        # Step 3: Make tenant non-nullable + add index
        migrations.AlterField(
            model_name="cardposition",
            name="tenant",
            field=models.ForeignKey(
                editable=False,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="kanban_cardposition_set",
                to="tenants.tenant",
                db_index=True,
            ),
        ),
    ]
