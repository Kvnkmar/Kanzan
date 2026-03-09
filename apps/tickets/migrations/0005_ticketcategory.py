from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0001_initial"),
        ("tickets", "0004_alter_ticketactivity_event"),
    ]

    operations = [
        migrations.CreateModel(
            name="TicketCategory",
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
                ("slug", models.SlugField(max_length=100)),
                ("color", models.CharField(default="#6c757d", max_length=7)),
                ("order", models.PositiveIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="%(class)ss",
                        to="tenants.tenant",
                    ),
                ),
            ],
            options={
                "verbose_name": "ticket category",
                "verbose_name_plural": "ticket categories",
                "ordering": ["order", "name"],
                "unique_together": {("tenant", "slug")},
            },
        ),
    ]
