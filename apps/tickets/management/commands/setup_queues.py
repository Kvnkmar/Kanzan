"""
Management command to seed default ticket queues for a tenant.

Usage::

    python manage.py setup_queues --tenant-slug demo

Creates the following queues (idempotent -- skips existing names):

    1. Support     -- General support requests
    2. Billing     -- Billing and payment issues
    3. Technical   -- Technical issues and bugs
    4. General     -- General inquiries
"""

from django.core.management.base import BaseCommand, CommandError

from apps.tenants.models import Tenant
from apps.tickets.models import Queue


DEFAULT_QUEUES = [
    {
        "name": "Support",
        "description": "General support requests and customer inquiries.",
    },
    {
        "name": "Billing",
        "description": "Billing, payment, and subscription issues.",
    },
    {
        "name": "Technical",
        "description": "Technical issues, bugs, and feature requests.",
    },
    {
        "name": "General",
        "description": "General inquiries and miscellaneous requests.",
    },
]


class Command(BaseCommand):
    help = "Create default ticket queues for a tenant."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant-slug",
            type=str,
            required=True,
            help="Slug of the tenant to create queues for.",
        )

    def handle(self, *args, **options):
        slug = options["tenant_slug"]

        try:
            tenant = Tenant.objects.get(slug=slug)
        except Tenant.DoesNotExist:
            raise CommandError(f'Tenant with slug "{slug}" does not exist.')

        created_count = 0
        skipped_count = 0

        for queue_data in DEFAULT_QUEUES:
            _, created = Queue.unscoped.get_or_create(
                tenant=tenant,
                name=queue_data["name"],
                defaults={
                    "description": queue_data["description"],
                },
            )
            if created:
                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(f'  Created queue: {queue_data["name"]}')
                )
            else:
                skipped_count += 1
                self.stdout.write(
                    f'  Skipped (already exists): {queue_data["name"]}'
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. Created {created_count}, skipped {skipped_count} "
                f'for tenant "{tenant.name}".'
            )
        )
