"""
Signals for VoIP CRM integration.

Auto-logs call events to Contact and Ticket timelines when calls
complete, and triggers recording processing for recorded calls.
"""

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.voip.models import CallLog

logger = logging.getLogger(__name__)

# Terminal call statuses that trigger timeline logging
_TERMINAL_STATUSES = frozenset({
    CallLog.Status.COMPLETED,
    CallLog.Status.MISSED,
    CallLog.Status.FAILED,
    CallLog.Status.BUSY,
    CallLog.Status.NO_ANSWER,
    CallLog.Status.VOICEMAIL,
})


@receiver(post_save, sender=CallLog)
def log_call_to_timelines(sender, instance, created, **kwargs):
    """Log completed/missed calls to Contact and Ticket timelines."""
    if created:
        return  # Only process status updates, not initial creation

    if instance.status not in _TERMINAL_STATUSES:
        return

    # Avoid duplicate logging via flag
    if getattr(instance, "_timeline_logged", False):
        return
    instance._timeline_logged = True

    _log_to_ticket_timeline(instance)
    _log_to_contact_timeline(instance)

    # Trigger recording processing if applicable
    if instance.status == CallLog.Status.COMPLETED and instance.duration_seconds > 0:
        _trigger_recording_processing(instance)


def _log_to_ticket_timeline(call_log):
    """Create a TicketActivity entry for the call."""
    if not call_log.ticket_id:
        return

    from apps.tickets.models import TicketActivity

    if call_log.direction == CallLog.Direction.OUTBOUND:
        event = (
            TicketActivity.Event.OUTBOUND_CALL_COMPLETED
            if call_log.status == CallLog.Status.COMPLETED
            else TicketActivity.Event.OUTBOUND_CALL
        )
    else:
        event = (
            TicketActivity.Event.INBOUND_CALL_COMPLETED
            if call_log.status == CallLog.Status.COMPLETED
            else TicketActivity.Event.INBOUND_CALL
        )

    duration_str = _format_duration(call_log.duration_seconds)
    description = (
        f"{call_log.get_direction_display()} call "
        f"({call_log.caller_number} → {call_log.callee_number}) — "
        f"{call_log.get_status_display()}"
    )
    if call_log.duration_seconds > 0:
        description += f" ({duration_str})"

    actor = None
    if call_log.caller_extension:
        actor = call_log.caller_extension.user
    elif call_log.callee_extension:
        actor = call_log.callee_extension.user

    try:
        TicketActivity.objects.create(
            tenant=call_log.tenant,
            ticket=call_log.ticket,
            event=event,
            description=description,
            actor=actor,
            contact=call_log.contact,
            data={
                "call_log_id": str(call_log.id),
                "direction": call_log.direction,
                "duration_seconds": call_log.duration_seconds,
                "status": call_log.status,
            },
        )
    except Exception:
        logger.exception("Failed to log call to ticket timeline: %s", call_log.id)


def _log_to_contact_timeline(call_log):
    """Create a contact activity entry for the call."""
    if not call_log.contact_id:
        return

    from apps.comments.models import ActivityLog
    from django.contrib.contenttypes.models import ContentType

    ct = ContentType.objects.get_for_model(call_log.contact)
    duration_str = _format_duration(call_log.duration_seconds)

    description = (
        f"{call_log.get_direction_display()} call "
        f"({call_log.get_status_display()})"
    )
    if call_log.duration_seconds > 0:
        description += f" — {duration_str}"

    actor = None
    if call_log.caller_extension:
        actor = call_log.caller_extension.user
    elif call_log.callee_extension:
        actor = call_log.callee_extension.user

    try:
        ActivityLog.objects.create(
            tenant=call_log.tenant,
            content_type=ct,
            object_id=call_log.contact_id,
            action=f"call_{call_log.status}",
            description=description,
            actor=actor,
            changes={
                "call_log_id": str(call_log.id),
                "direction": call_log.direction,
                "duration_seconds": call_log.duration_seconds,
                "caller_number": call_log.caller_number,
                "callee_number": call_log.callee_number,
            },
        )
    except Exception:
        logger.exception("Failed to log call to contact timeline: %s", call_log.id)


def _trigger_recording_processing(call_log):
    """Queue recording download if recording is enabled for the tenant."""
    from apps.voip.models import VoIPSettings

    try:
        settings = VoIPSettings.objects.get(
            tenant=call_log.tenant,
            is_active=True,
            recording_enabled=True,
        )
    except VoIPSettings.DoesNotExist:
        return

    from apps.voip.tasks import process_call_recording

    process_call_recording.delay(str(call_log.id))


def _format_duration(seconds):
    """Format seconds as human-readable duration."""
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    secs = seconds % 60
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m"
