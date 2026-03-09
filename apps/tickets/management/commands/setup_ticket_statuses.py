"""
Management command to seed default ticket statuses for a tenant.

Usage::

    python manage.py setup_ticket_statuses --tenant-slug demo

Creates the following statuses (idempotent -- skips existing slugs):

    1. Open        (#0d6efd)  -- default for new tickets
    2. In Progress (#ffc107)
    3. Waiting     (#6c757d)
    4. Resolved    (#198754)  -- closed state
    5. Closed      (#dc3545)  -- closed state
"""

from django.core.management.base import BaseCommand, CommandError

from apps.tenants.models import Tenant
from apps.tickets.models import TicketStatus


DEFAULT_STATUSES = [
    {
        "name": "Open",
        "slug": "open",
        "color": "#0d6efd",
        "order": 10,
        "is_closed": False,
        "is_default": True,
    },
    {
        "name": "In Progress",
        "slug": "in-progress",
        "color": "#ffc107",
        "order": 20,
        "is_closed": False,
        "is_default": False,
    },
    {
        "name": "Waiting",
        "slug": "waiting",
        "color": "#6c757d",
        "order": 30,
        "is_closed": False,
        "is_default": False,
    },
    {
        "name": "Resolved",
        "slug": "resolved",
        "color": "#198754",
        "order": 40,
        "is_closed": True,
        "is_default": False,
    },
    {
        "name": "Closed",
        "slug": "closed",
        "color": "#dc3545",
        "order": 50,
        "is_closed": True,
        "is_default": False,
    },
]


class Command(BaseCommand):
    help = "Create default ticket statuses for a tenant."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant-slug",
            type=str,
            required=True,
            help="Slug of the tenant to create statuses for.",
        )

    def handle(self, *args, **options):
        slug = options["tenant_slug"]

        try:
            tenant = Tenant.objects.get(slug=slug)
        except Tenant.DoesNotExist:
            raise CommandError(f'Tenant with slug "{slug}" does not exist.')

        created_count = 0
        skipped_count = 0

        for status_data in DEFAULT_STATUSES:
            _, created = TicketStatus.unscoped.get_or_create(
                tenant=tenant,
                slug=status_data["slug"],
                defaults={
                    "name": status_data["name"],
                    "color": status_data["color"],
                    "order": status_data["order"],
                    "is_closed": status_data["is_closed"],
                    "is_default": status_data["is_default"],
                },
            )
            if created:
                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(f'  Created status: {status_data["name"]}')
                )
            else:
                skipped_count += 1
                self.stdout.write(
                    f'  Skipped (already exists): {status_data["name"]}'
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. Created {created_count}, skipped {skipped_count} "
                f"for tenant \"{tenant.name}\"."
            )
        )
