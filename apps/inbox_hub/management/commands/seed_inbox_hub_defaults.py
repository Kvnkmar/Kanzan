"""Seed Inbox Hub defaults for a tenant.

Creates a "General" department (lead = tenant admin), enrolls active non-viewer
members so auto-assignment has candidates, links a sensible default queue, and
points ``TenantSettings.inbox_hub_default_department`` at it so emails matching
no routing rule have somewhere to land.

Run this once per tenant before (or just after) flipping ``inbox_hub_enabled``::

    python manage.py seed_inbox_hub_defaults --tenant-slug straat-x
    python manage.py seed_inbox_hub_defaults --all-tenants

Idempotent — safe to re-run; it tops up missing memberships and never
duplicates the department.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import slugify

from apps.accounts.models import TenantMembership
from apps.inbox_hub.models import Department, DepartmentMembership
from apps.tenants.models import Tenant

GENERAL_SLUG = "general"
VIEWER_LEVEL = 40


class Command(BaseCommand):
    help = "Create the default 'General' department and wire it as the Inbox Hub fallback."

    def add_arguments(self, parser):
        parser.add_argument("--tenant-slug", type=str, help="Slug of a single tenant to seed.")
        parser.add_argument("--all-tenants", action="store_true", help="Seed every active tenant.")

    def handle(self, *args, **options):
        slug = options.get("tenant_slug")
        all_tenants = options.get("all_tenants")
        if not slug and not all_tenants:
            raise CommandError("Provide --tenant-slug <slug> or --all-tenants.")

        if all_tenants:
            tenants = list(Tenant.objects.filter(is_active=True))
        else:
            try:
                tenants = [Tenant.objects.get(slug=slug)]
            except Tenant.DoesNotExist:
                raise CommandError(f'Tenant with slug "{slug}" does not exist.')

        for tenant in tenants:
            self._seed_tenant(tenant)

    def _seed_tenant(self, tenant):
        self.stdout.write(f"Seeding Inbox Hub defaults for {tenant.name} ({tenant.slug})...")

        memberships = list(
            TenantMembership.objects
            .filter(tenant=tenant, is_active=True)
            .select_related("role", "user")
        )
        if not memberships:
            self.stdout.write(self.style.WARNING("  No active members — skipping (need a lead user)."))
            return

        # Lead = lowest hierarchy_level member (admin first), so PROTECT is satisfied.
        lead_membership = min(memberships, key=lambda m: m.role.hierarchy_level)
        lead = lead_membership.user

        default_queue = self._pick_default_queue(tenant)

        with transaction.atomic():
            dept, created = Department.unscoped.get_or_create(
                tenant=tenant,
                slug=GENERAL_SLUG,
                defaults={
                    "name": "General",
                    "description": "Default department for unrouted inbound email.",
                    "lead": lead,
                    "default_queue": default_queue,
                    "is_active": True,
                },
            )
            verb = "Created" if created else "Found"
            self.stdout.write(self.style.SUCCESS(f"  {verb} department: {dept.name} (lead={lead.email})"))

            # Backfill a default queue if the department predates one.
            if not dept.default_queue_id and default_queue is not None:
                dept.default_queue = default_queue
                dept.save(update_fields=["default_queue", "updated_at"])

            # Enroll active non-viewer members so assignment has candidates.
            added = 0
            for m in memberships:
                if m.role.hierarchy_level >= VIEWER_LEVEL:
                    continue
                _, was_created = DepartmentMembership.unscoped.get_or_create(
                    tenant=tenant, department=dept, user=m.user,
                )
                added += int(was_created)
            self.stdout.write(f"  Enrolled {added} new member(s).")

            # Point the tenant's settings at this department as the fallback.
            settings_obj = getattr(tenant, "settings", None)
            if settings_obj is not None and settings_obj.inbox_hub_default_department_id != dept.id:
                settings_obj.inbox_hub_default_department = dept
                settings_obj.save(update_fields=["inbox_hub_default_department", "updated_at"])
                self.stdout.write("  Set as Inbox Hub default department.")

    def _pick_default_queue(self, tenant):
        from apps.tickets.models import Queue

        for name in ("General", "Support"):
            q = Queue.unscoped.filter(tenant=tenant, name=name).first()
            if q is not None:
                return q
        return Queue.unscoped.filter(tenant=tenant).order_by("created_at").first()
