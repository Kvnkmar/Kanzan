"""
Nightly CRM scoring tasks and periodic reminder overdue checks.

Tasks:
    calculate_lead_scores
        Scores every Contact (0-100) based on recent engagement signals.
        Scheduled nightly via Celery Beat.

    calculate_account_health_scores
        Scores every Account (0-100) based on CSAT, SLA, and activity signals.
        Scheduled nightly via Celery Beat.

    fire_due_reminders
        Fires a one-shot "reminder due" popup notification the moment a
        reminder's scheduled time arrives. Scheduled every 30s via Celery Beat.

    check_overdue_reminders
        Periodic scan for overdue reminders. Sends REMINDER_OVERDUE
        notifications to assignees. Deduplicates per reminder per day.
        (Implemented; not currently registered in CELERY_BEAT_SCHEDULE.)
"""

import logging
from datetime import timedelta

from celery import shared_task
from django.db.models import Avg, F, Q
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=1, acks_late=True)
def fire_due_reminders(self):
    """
    Send a one-shot "reminder due" notification at the moment a reminder
    comes due, so the owner gets a popup and never misses it.

    Distinct from ``check_overdue_reminders`` (which nags about already-late
    reminders once per day): this fires exactly once, right as the clock
    passes ``scheduled_at``.

    Fire condition (per reminder):
        scheduled_at <= now                  (it's due)
        AND completed_at IS NULL             (not done)
        AND cancelled_at IS NULL             (not cancelled)
        AND (due_notified_at IS NULL OR due_notified_at < scheduled_at)

    The ``due_notified_at < scheduled_at`` half is the re-arm guard: after we
    fire we stamp ``due_notified_at = now`` (>= scheduled_at), so it won't
    re-fire. But if the reminder is later moved forward (via the reschedule
    action OR a raw PATCH of scheduled_at), the old stamp falls behind the new
    time and the alert re-arms automatically — no flag reset needed anywhere.

    Recipient: the assignee, or the creator when unassigned (created_by is
    PROTECT, so there is always a recipient). Uses ``Model.unscoped`` because
    Celery tasks run without a thread-local tenant.
    """
    from apps.crm.models import Reminder
    from apps.notifications.models import NotificationType
    from apps.notifications.services import send_notification
    from apps.tenants.live import broadcast_live_event
    from apps.tenants.models import Tenant

    now = timezone.now()
    total_notified = 0

    for tenant in Tenant.objects.filter(is_active=True).iterator():
        try:
            due_reminders = (
                Reminder.unscoped.filter(
                    tenant=tenant,
                    scheduled_at__lte=now,
                    completed_at__isnull=True,
                    cancelled_at__isnull=True,
                )
                .filter(
                    Q(due_notified_at__isnull=True)
                    | Q(due_notified_at__lt=F("scheduled_at"))
                )
                .select_related("assigned_to", "created_by")
                .prefetch_related("contacts")
                .iterator(chunk_size=200)
            )

            for reminder in due_reminders:
                # Isolate each reminder so one bad row doesn't skip the rest of
                # the tenant's due batch this tick.
                try:
                    recipient = reminder.assigned_to or reminder.created_by
                    if recipient is None:
                        continue

                    # Claim FIRST: stamp the dedup/re-arm watermark before
                    # notifying. The task runs outside an atomic block, so
                    # send_notification pushes synchronously — if we notified
                    # first and the watermark write then failed, the alert would
                    # re-fire every tick. Stamping first means a transient
                    # downstream failure costs at most one missed alert, never a
                    # duplicate loop. .update() (not save()) avoids re-tripping
                    # the reminder post_save signals/broadcast.
                    Reminder.unscoped.filter(pk=reminder.pk).update(
                        due_notified_at=now
                    )

                    linked_contacts = list(reminder.contacts.all())
                    contact_name = ", ".join(
                        c.full_name for c in linked_contacts if c.full_name
                    ) or None

                    body = (
                        f"Reminder for {contact_name} is due now."
                        if contact_name
                        else "Your reminder is due now."
                    )

                    send_notification(
                        tenant=tenant,
                        recipient=recipient,
                        notification_type=NotificationType.REMINDER_DUE,
                        title=f"Reminder due: {reminder.subject}",
                        body=body,
                        data={
                            "reminder_id": str(reminder.pk),
                            "contact_ids": [str(c.id) for c in linked_contacts],
                            "priority": reminder.priority,
                            "scheduled_at": reminder.scheduled_at.isoformat(),
                            "url": "/reminders/",
                        },
                    )
                    total_notified += 1

                    # Tenant-wide live nudge so the reminders workspace can pull
                    # the row into its "overdue/now" group instantly.
                    # recipient_id lets the page filter to the current user.
                    broadcast_live_event(
                        tenant,
                        "reminder.due",
                        {
                            "id": str(reminder.pk),
                            "subject": reminder.subject,
                            "priority": reminder.priority,
                            "scheduled_at": reminder.scheduled_at.isoformat(),
                            "recipient_id": str(recipient.id),
                        },
                    )
                except Exception:
                    logger.exception(
                        "fire_due_reminders: failed to alert reminder %s",
                        reminder.pk,
                    )

        except Exception:
            logger.exception(
                "fire_due_reminders failed for tenant %s", tenant.slug
            )

    logger.info(
        "fire_due_reminders complete: %d notifications sent.", total_notified
    )


@shared_task(bind=True, max_retries=1, acks_late=True)
def check_overdue_reminders(self):
    """
    Scan all active tenants for overdue reminders and send notifications.

    Dedup: one REMINDER_OVERDUE notification per reminder per day.
    Uses Model.unscoped since Celery tasks run without thread-local tenant.

    Optional escalation: if overdue > 24h and assigned_to has a manager,
    notify managers in the tenant (reuses existing notification infra).
    """
    from apps.crm.models import Reminder
    from apps.notifications.models import Notification, NotificationType
    from apps.notifications.services import send_notification
    from apps.tenants.models import Tenant

    now = timezone.now()
    one_day_ago = now - timedelta(days=1)

    active_tenants = Tenant.objects.filter(is_active=True)

    total_notified = 0

    for tenant in active_tenants.iterator():
        try:
            overdue_reminders = (
                Reminder.unscoped.filter(
                    tenant=tenant,
                    scheduled_at__lt=now,
                    completed_at__isnull=True,
                    cancelled_at__isnull=True,
                )
                .select_related("assigned_to")
                .prefetch_related("contacts")
                .iterator(chunk_size=200)
            )

            for reminder in overdue_reminders:
                if not reminder.assigned_to:
                    continue

                # Dedup: check if we already sent a REMINDER_OVERDUE
                # notification for this reminder today.
                already_notified = Notification.unscoped.filter(
                    tenant=tenant,
                    recipient=reminder.assigned_to,
                    type=NotificationType.REMINDER_OVERDUE,
                    data__reminder_id=str(reminder.pk),
                    created_at__gte=one_day_ago,
                ).exists()

                if already_notified:
                    continue

                linked_contacts = list(reminder.contacts.all())
                if linked_contacts:
                    contact_name = ", ".join(
                        c.full_name for c in linked_contacts if c.full_name
                    ) or "Unknown"
                else:
                    contact_name = "Unknown"
                overdue_mins = int(
                    (now - reminder.scheduled_at).total_seconds() / 60
                )
                if overdue_mins >= 1440:
                    overdue_display = f"{overdue_mins // 1440}d overdue"
                elif overdue_mins >= 60:
                    overdue_display = f"{overdue_mins // 60}h overdue"
                else:
                    overdue_display = f"{overdue_mins}m overdue"

                send_notification(
                    tenant=tenant,
                    recipient=reminder.assigned_to,
                    notification_type=NotificationType.REMINDER_OVERDUE,
                    title=f"Overdue reminder: {reminder.subject}",
                    body=(
                        f"Your reminder for {contact_name} is "
                        f"{overdue_display}. Scheduled at "
                        f"{reminder.scheduled_at.strftime('%Y-%m-%d %H:%M')}."
                    ),
                    data={
                        "reminder_id": str(reminder.pk),
                        "contact_ids": [str(c.id) for c in linked_contacts],
                        "url": "/reminders/",
                    },
                )
                total_notified += 1

                # Escalation: if overdue > 24h, notify tenant admins/managers
                if reminder.scheduled_at < now - timedelta(hours=24):
                    _escalate_overdue_reminder(
                        tenant, reminder, contact_name, overdue_display, now
                    )

        except Exception:
            logger.exception(
                "check_overdue_reminders failed for tenant %s", tenant.slug
            )

    logger.info(
        "check_overdue_reminders complete: %d notifications sent.", total_notified
    )


def _escalate_overdue_reminder(tenant, reminder, contact_name, overdue_display, now):
    """Notify managers about severely overdue reminders (>24h)."""
    from apps.accounts.models import TenantMembership
    from apps.notifications.models import Notification, NotificationType
    from apps.notifications.services import send_notification

    one_day_ago = now - timedelta(days=1)

    managers = TenantMembership.objects.filter(
        tenant=tenant,
        is_active=True,
        role__hierarchy_level__lte=20,
    ).exclude(
        user=reminder.assigned_to,
    ).select_related("user")

    for membership in managers:
        already = Notification.unscoped.filter(
            tenant=tenant,
            recipient=membership.user,
            type=NotificationType.REMINDER_OVERDUE,
            data__reminder_id=str(reminder.pk),
            created_at__gte=one_day_ago,
        ).exists()

        if already:
            continue

        assignee_name = (
            reminder.assigned_to.get_full_name()
            if reminder.assigned_to
            else "Unassigned"
        )
        send_notification(
            tenant=tenant,
            recipient=membership.user,
            notification_type=NotificationType.REMINDER_OVERDUE,
            title=f"Escalation: Overdue reminder ({overdue_display})",
            body=(
                f"Reminder '{reminder.subject}' for {contact_name} "
                f"assigned to {assignee_name} is {overdue_display}."
            ),
            data={
                "reminder_id": str(reminder.pk),
                "escalation": True,
                "url": "/reminders/",
            },
        )


@shared_task(bind=True, max_retries=1, acks_late=True)
def calculate_lead_scores(self):
    """
    Calculate lead scores for all contacts across all active tenants.

    Algorithm per contact:
        Base: 50
        +20 if ContactEvent in last 7 days
        +15 if Activity completed in last 14 days
        +10 if open ticket with ticket_type=deal
        -20 if last_activity_at is null or > 30 days ago
        -15 if any ticket.csat_rating <= 2 in last 90 days
        Clamped to 0-100
    """
    from apps.contacts.models import Contact, ContactEvent
    from apps.crm.models import Activity
    from apps.tenants.models import Tenant
    from apps.tickets.models import Ticket, TicketStatus

    now = timezone.now()
    seven_days_ago = now - timedelta(days=7)
    fourteen_days_ago = now - timedelta(days=14)
    thirty_days_ago = now - timedelta(days=30)
    ninety_days_ago = now - timedelta(days=90)

    tenant_count = 0
    total_updated = 0

    for tenant in Tenant.objects.filter(is_active=True).iterator():
        tenant_count += 1

        contacts = Contact.unscoped.filter(tenant=tenant)
        if not contacts.exists():
            continue

        # Pre-fetch sets of contact IDs matching each signal
        contacts_with_recent_event = set(
            ContactEvent.unscoped.filter(
                tenant=tenant,
                occurred_at__gte=seven_days_ago,
            ).values_list("contact_id", flat=True)
        )

        contacts_with_completed_activity = set(
            Activity.unscoped.filter(
                tenant=tenant,
                completed_at__gte=fourteen_days_ago,
                completed_at__isnull=False,
            ).values_list("contact_id", flat=True)
        )

        closed_status_ids = set(
            TicketStatus.unscoped.filter(
                tenant=tenant, is_closed=True,
            ).values_list("id", flat=True)
        )

        contacts_with_open_deal = set(
            Ticket.unscoped.filter(
                tenant=tenant,
                ticket_type="deal",
            ).exclude(
                status_id__in=closed_status_ids,
            ).exclude(
                contact__isnull=True,
            ).values_list("contact_id", flat=True)
        )

        contacts_with_bad_csat = set(
            Ticket.unscoped.filter(
                tenant=tenant,
                csat_rating__lte=2,
                csat_submitted_at__gte=ninety_days_ago,
            ).exclude(
                contact__isnull=True,
            ).values_list("contact_id", flat=True)
        )

        # Score buckets for bulk update
        score_buckets = {}  # score -> list of contact PKs

        for contact in contacts.iterator(chunk_size=500):
            score = 50

            if contact.pk in contacts_with_recent_event:
                score += 20
            if contact.pk in contacts_with_completed_activity:
                score += 15
            if contact.pk in contacts_with_open_deal:
                score += 10
            if contact.last_activity_at is None or contact.last_activity_at < thirty_days_ago:
                score -= 20
            if contact.pk in contacts_with_bad_csat:
                score -= 15

            score = max(0, min(100, score))
            score_buckets.setdefault(score, []).append(contact.pk)

        # Bulk update per score value
        for score_val, pks in score_buckets.items():
            updated = Contact.unscoped.filter(pk__in=pks).update(lead_score=score_val)
            total_updated += updated

    logger.info(
        "calculate_lead_scores complete: %d tenants, %d contacts updated.",
        tenant_count,
        total_updated,
    )


@shared_task(bind=True, max_retries=1, acks_late=True)
def calculate_account_health_scores(self):
    """
    Calculate health scores for all accounts across all active tenants.

    Algorithm per account:
        Base: 50
        Average CSAT across all contacts' tickets in last 90 days
            → map 1-5 to 0-40 points
        +10 if any open deal ticket with pipeline_stage.is_won=False
            and expected_close_date > today
        -20 if any ticket SLA breached in last 30 days
        -15 if no contact activity in last 30 days
        Clamped to 0-100
    """
    from apps.contacts.models import Account
    from apps.tenants.models import Tenant
    from apps.tickets.models import Ticket, TicketStatus

    now = timezone.now()
    today = now.date()
    thirty_days_ago = now - timedelta(days=30)
    ninety_days_ago = now - timedelta(days=90)

    tenant_count = 0
    total_updated = 0

    for tenant in Tenant.objects.filter(is_active=True).iterator():
        tenant_count += 1

        accounts = Account.unscoped.filter(tenant=tenant)
        if not accounts.exists():
            continue

        closed_status_ids = set(
            TicketStatus.unscoped.filter(
                tenant=tenant, is_closed=True,
            ).values_list("id", flat=True)
        )

        score_buckets = {}

        for account in accounts.iterator(chunk_size=200):
            score = 50

            # --- CSAT component (0-40 points) ---
            avg_csat = Ticket.unscoped.filter(
                tenant=tenant,
                contact__account=account,
                csat_rating__isnull=False,
                csat_submitted_at__gte=ninety_days_ago,
            ).aggregate(avg=Avg("csat_rating"))["avg"]

            if avg_csat is not None:
                # Map 1-5 → 0-40: (avg - 1) / 4 * 40
                csat_points = (avg_csat - 1) / 4 * 40
                score += csat_points
            # If no CSAT data, no bonus/penalty from this component

            # --- Active deal pipeline (+10) ---
            has_active_deal = Ticket.unscoped.filter(
                tenant=tenant,
                account=account,
                ticket_type="deal",
                pipeline_stage__isnull=False,
                pipeline_stage__is_won=False,
                expected_close_date__gt=today,
            ).exclude(
                status_id__in=closed_status_ids,
            ).exists()

            if has_active_deal:
                score += 10

            # --- SLA breach in last 30 days (-20) ---
            has_sla_breach = Ticket.unscoped.filter(
                tenant=tenant,
                contact__account=account,
                updated_at__gte=thirty_days_ago,
            ).filter(
                Q(sla_response_breached=True) | Q(sla_resolution_breached=True),
            ).exists()

            if has_sla_breach:
                score -= 20

            # --- No contact activity in last 30 days (-15) ---
            has_recent_activity = account.contacts.filter(
                last_activity_at__gte=thirty_days_ago,
            ).exists()

            if not has_recent_activity:
                score -= 15

            score = max(0, min(100, int(score)))
            score_buckets.setdefault(score, []).append(account.pk)

        for score_val, pks in score_buckets.items():
            updated = Account.unscoped.filter(pk__in=pks).update(health_score=score_val)
            total_updated += updated

    logger.info(
        "calculate_account_health_scores complete: %d tenants, %d accounts updated.",
        tenant_count,
        total_updated,
    )
