"""
Phase 2: Add CannedResponse and SavedView models.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("tickets", "0006_ticket_first_responded_at_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="CannedResponse",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "title",
                    models.CharField(
                        help_text="Display name for the response", max_length=200
                    ),
                ),
                (
                    "content",
                    models.TextField(
                        help_text="Response content. Supports template variables like {{ticket.number}}."
                    ),
                ),
                (
                    "category",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="Grouping label, e.g. 'Billing', 'Technical', 'General'.",
                        max_length=100,
                    ),
                ),
                (
                    "shortcut",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="Quick trigger like '/thanks' or '/refund'.",
                        max_length=20,
                    ),
                ),
                (
                    "is_shared",
                    models.BooleanField(
                        default=True,
                        help_text="False = personal to creator only.",
                    ),
                ),
                ("usage_count", models.PositiveIntegerField(default=0)),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="canned_responses",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        db_index=True,
                        editable=False,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="%(app_label)s_%(class)s_set",
                        to="tenants.tenant",
                    ),
                ),
            ],
            options={
                "ordering": ["category", "title"],
            },
        ),
        migrations.AddIndex(
            model_name="cannedresponse",
            index=models.Index(
                fields=["tenant", "is_shared"],
                name="tickets_can_tenant__83e6de_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="cannedresponse",
            index=models.Index(
                fields=["shortcut"],
                name="tickets_can_shortcu_d9f3a5_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="cannedresponse",
            constraint=models.UniqueConstraint(
                condition=~models.Q(("shortcut", "")),
                fields=("tenant", "shortcut"),
                name="unique_shortcut_per_tenant",
            ),
        ),
        migrations.CreateModel(
            name="SavedView",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=100)),
                (
                    "resource_type",
                    models.CharField(
                        choices=[("ticket", "Tickets"), ("contact", "Contacts")],
                        max_length=20,
                    ),
                ),
                (
                    "filters",
                    models.JSONField(
                        default=dict,
                        help_text="Filter parameters as JSON",
                    ),
                ),
                (
                    "sort_field",
                    models.CharField(default="-created_at", max_length=50),
                ),
                (
                    "is_default",
                    models.BooleanField(
                        default=False,
                        help_text="Load this view by default.",
                    ),
                ),
                (
                    "is_pinned",
                    models.BooleanField(
                        default=False,
                        help_text="Pin to top of view selector.",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        help_text="null = shared view visible to all tenant members.",
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="saved_views",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        db_index=True,
                        editable=False,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="%(app_label)s_%(class)s_set",
                        to="tenants.tenant",
                    ),
                ),
            ],
            options={
                "ordering": ["-is_pinned", "name"],
            },
        ),
        migrations.AddIndex(
            model_name="savedview",
            index=models.Index(
                fields=["tenant", "resource_type", "user"],
                name="tickets_sav_tenant__a1c2b3_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="savedview",
            constraint=models.UniqueConstraint(
                fields=("tenant", "user", "name", "resource_type"),
                name="unique_view_per_user_resource",
            ),
        ),
    ]
