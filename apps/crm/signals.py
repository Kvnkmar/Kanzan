"""Signal handlers that broadcast CRM activity / reminder changes live.

These power the "real-time CRM" feel: when an agent creates an activity
or marks a reminder complete, every other connected client in the same
tenant sees the dashboards, lists, and counters update without reload.

All payloads are minimal -- enough for the client to either patch an
existing card in-place or trigger a small refetch.  The handlers defer
the broadcast to ``transaction.on_commit`` so rollbacks don't leak.
"""

from __future__ import annotations

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.crm.models import Activity, Reminder
from apps.tenants.live import broadcast_live_event


def _serialise_activity(act: Activity) -> dict:
    return {
        "id":            str(act.id),
        "type":          act.activity_type,
        "subject":       act.subject,
        "due_at":        act.due_at.isoformat() if act.due_at else None,
        "completed_at":  act.completed_at.isoformat() if act.completed_at else None,
        "outcome":       act.outcome,
        "ticket_id":     str(act.ticket_id) if act.ticket_id else None,
        "contact_id":    str(act.contact_id) if act.contact_id else None,
        "assigned_to":   str(act.assigned_to_id) if act.assigned_to_id else None,
        "created_at":    act.created_at.isoformat() if act.created_at else None,
        "updated_at":    act.updated_at.isoformat() if act.updated_at else None,
    }


def _serialise_reminder(rem: Reminder) -> dict:
    return {
        "id":            str(rem.id),
        "subject":       rem.subject,
        "priority":      rem.priority,
        "scheduled_at":  rem.scheduled_at.isoformat() if rem.scheduled_at else None,
        "completed_at":  rem.completed_at.isoformat() if rem.completed_at else None,
        "cancelled_at":  rem.cancelled_at.isoformat() if rem.cancelled_at else None,
        "assigned_to":   str(rem.assigned_to_id) if rem.assigned_to_id else None,
        # status is a computed property; expose it so clients can render
        # the right badge without re-deriving from the timestamps.
        "status":        getattr(rem, "status", None),
        "created_at":    rem.created_at.isoformat() if rem.created_at else None,
        "updated_at":    rem.updated_at.isoformat() if rem.updated_at else None,
    }


# ── Activity ───────────────────────────────────────────────────────────

@receiver(post_save, sender=Activity)
def broadcast_activity_save(sender, instance, created, **kwargs):
    event = "activity.created" if created else "activity.updated"
    broadcast_live_event(
        tenant=instance.tenant,
        event=event,
        payload=_serialise_activity(instance),
    )


@receiver(post_delete, sender=Activity)
def broadcast_activity_delete(sender, instance, **kwargs):
    broadcast_live_event(
        tenant=instance.tenant,
        event="activity.deleted",
        payload={"id": str(instance.id)},
    )


# ── Reminder ───────────────────────────────────────────────────────────

@receiver(post_save, sender=Reminder)
def broadcast_reminder_save(sender, instance, created, **kwargs):
    """Fire a typed reminder event.

    We pick the verb based on the most informative state change so the
    client doesn't have to diff timestamps to know whether a reminder
    was just completed, cancelled, rescheduled, or freshly created.
    """
    if created:
        event = "reminder.created"
    elif instance.completed_at is not None:
        event = "reminder.completed"
    elif instance.cancelled_at is not None:
        event = "reminder.cancelled"
    else:
        event = "reminder.updated"

    broadcast_live_event(
        tenant=instance.tenant,
        event=event,
        payload=_serialise_reminder(instance),
    )


@receiver(post_delete, sender=Reminder)
def broadcast_reminder_delete(sender, instance, **kwargs):
    broadcast_live_event(
        tenant=instance.tenant,
        event="reminder.deleted",
        payload={"id": str(instance.id)},
    )
