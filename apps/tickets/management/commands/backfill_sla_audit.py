"""
Management command to write a baseline ActivityLog entry for all in-flight
tickets that have SLA deadlines set.

Run once after deploying the SLA audit logging feature so that future diffs
have a starting point (a "before" snapshot).

Usage:
    python manage.py backfill_sla_audit [--tenant-slug SLUG] [--dry-run]
"""

import logging

from django.core.management.base import BaseCommand

from apps.comments.models import ActivityLog
from apps.comments.services import log_activity
from apps.tenants.models import Tenant
from apps.tickets.models import Ticket, TicketActivity

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Write baseline SLA audit log entries for all in-flight tickets with SLA deadlines."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant-slug",
            type=str,
            default=None,
            help="Limit to a single tenant by slug. If omitted, all active tenants are processed.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be written without actually creating log entries.",
        )

    def handle(self, *args, **options):
        tenant_slug = options["tenant_slug"]
        dry_run = options["dry_run"]

        tenants = Tenant.objects.filter(is_active=True)
        if tenant_slug:
            tenants = tenants.filter(slug=tenant_slug)

        total = 0

        for tenant in tenants:
            tickets = (
                Ticket.unscoped
                .filter(
                    tenant=tenant,
                    status__is_closed=False,
                )
                .exclude(
                    sla_first_response_due__isnull=True,
                    sla_resolution_due__isnull=True,
                )
                .select_related("sla_policy", "status")
            )

            count = tickets.count()
            if count == 0:
                continue

            self.stdout.write(
                f"Tenant '{tenant.slug}': {count} in-flight ticket(s) with SLA deadlines"
            )

            for ticket in tickets.iterator(chunk_size=200):
                def _fmt(dt):
                    return dt.isoformat() if dt else None

                changes = {
                    "sla_first_response_due": {
                        "before": None,
                        "after": _fmt(ticket.sla_first_response_due),
                    },
                    "sla_resolution_due": {
                        "before": None,
                        "after": _fmt(ticket.sla_resolution_due),
                    },
                    "triggered_by": "backfill",
                }

                policy_name = ticket.sla_policy.name if ticket.sla_policy else "none"
                msg = (
                    f"SLA baseline snapshot: first response due "
                    f"{_fmt(ticket.sla_first_response_due)}, resolution due "
                    f"{_fmt(ticket.sla_resolution_due)} (policy: {policy_name})"
                )

                if dry_run:
                    self.stdout.write(f"  [DRY RUN] Ticket #{ticket.number}: {msg}")
                else:
                    try:
                        log_activity(
                            tenant=tenant,
                            actor=None,
                            content_object=ticket,
                            action=ActivityLog.Action.SLA_UPDATED,
                            description=msg,
                            changes=changes,
                        )
                        TicketActivity.objects.create(
                            tenant=tenant,
                            ticket=ticket,
                            actor=None,
                            event=TicketActivity.Event.STATUS_CHANGED,
                            message=msg,
                            metadata=changes,
                        )
                    except Exception:
                        logger.exception(
                            "Failed to backfill SLA audit for ticket #%s",
                            ticket.number,
                        )
                        continue

                total += 1

        action = "Would write" if dry_run else "Wrote"
        self.stdout.write(
            self.style.SUCCESS(f"{action} baseline entries for {total} ticket(s).")
        )
