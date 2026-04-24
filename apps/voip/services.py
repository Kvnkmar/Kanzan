"""
VoIP service layer.

Orchestrates call operations between Django, Asterisk ARI, and the
WebSocket consumers. Handles billing enforcement and usage tracking.
"""

import asyncio
import logging

from django.db.models import F
from django.utils import timezone

from apps.voip.models import CallLog, Extension, VoIPSettings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Billing enforcement
# ---------------------------------------------------------------------------


def check_call_limit(tenant):
    """
    Check whether the tenant can make another call.

    Returns:
        (can_call: bool, reason: str | None)
    """
    from apps.billing.models import Subscription, UsageTracker

    try:
        sub = Subscription.objects.select_related("plan").get(tenant=tenant)
    except Subscription.DoesNotExist:
        return False, "No active subscription."

    plan = sub.plan
    if not plan.has_voip:
        return False, "VoIP is not available on your current plan."

    if plan.max_calls_per_month is None:
        return True, None  # Unlimited

    try:
        tracker = UsageTracker.objects.get(tenant=tenant)
    except UsageTracker.DoesNotExist:
        return True, None  # No tracker = no enforcement

    if tracker.calls_made >= plan.max_calls_per_month:
        return False, (
            f"Monthly call limit reached ({plan.max_calls_per_month} calls). "
            "Upgrade your plan for more calls."
        )

    return True, None


def increment_call_usage(tenant):
    """Atomically increment the call usage counter."""
    from apps.billing.models import UsageTracker

    UsageTracker.objects.filter(tenant=tenant).update(
        calls_made=F("calls_made") + 1,
        updated_at=timezone.now(),
    )


# ---------------------------------------------------------------------------
# ARI call operations (sync wrappers for async ARI client)
# ---------------------------------------------------------------------------


def _get_ari_client(tenant):
    """Create an ARI client for the given tenant's VoIP settings."""
    from apps.voip.ari_client import ARIClient

    try:
        settings = VoIPSettings.objects.get(tenant=tenant, is_active=True)
    except VoIPSettings.DoesNotExist:
        return None, "VoIP is not configured for this tenant."

    client = ARIClient(
        host=settings.asterisk_host,
        port=settings.asterisk_ari_port,
        username=settings.ari_username,
        password=settings.ari_password,
        use_ssl=settings.asterisk_use_ssl,
    )
    return client, None


def _run_async(coro):
    """Run an async coroutine from synchronous Django code."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result(timeout=15)
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


def originate_call(call_log, caller_extension):
    """
    Originate an outbound call via Asterisk ARI.

    Args:
        call_log: CallLog instance with callee_number set
        caller_extension: Extension instance for the caller

    Returns:
        (success: bool, error: str | None)
    """
    client, error = _get_ari_client(call_log.tenant)
    if error:
        return False, error

    caller_id = (
        caller_extension.caller_id_name or "Kanzen Suites"
    )
    if caller_extension.caller_id_number:
        caller_id = f'"{caller_id}" <{caller_extension.caller_id_number}>'

    async def _originate():
        try:
            result, err = await client.originate(
                endpoint=f"PJSIP/{caller_extension.sip_username}",
                caller_id=caller_id,
                app="kanzan-voip",
                app_args=f"outbound,{call_log.callee_number},{call_log.id}",
                variables={
                    "KANZAN_CALL_ID": str(call_log.id),
                    "KANZAN_TENANT_ID": str(call_log.tenant_id),
                    "KANZAN_DIRECTION": "outbound",
                    "KANZAN_CALLEE": call_log.callee_number,
                },
            )
            return result, err
        finally:
            await client.close()

    result, err = _run_async(_originate())
    if err:
        return False, err

    # Store the Asterisk channel ID
    if result and "id" in result:
        call_log.asterisk_channel_id = result["id"]
        call_log.save(update_fields=["asterisk_channel_id", "updated_at"])

    return True, None


def hangup_call(call_log):
    """Hang up an active call via ARI."""
    if not call_log.asterisk_channel_id:
        # No ARI channel — just update status locally
        call_log.status = CallLog.Status.COMPLETED
        call_log.ended_at = timezone.now()
        if call_log.answered_at:
            call_log.duration_seconds = int(
                (call_log.ended_at - call_log.answered_at).total_seconds()
            )
        call_log.save(update_fields=[
            "status", "ended_at", "duration_seconds", "updated_at",
        ])
        return True, None

    client, error = _get_ari_client(call_log.tenant)
    if error:
        return False, error

    async def _hangup():
        try:
            return await client.hangup(call_log.asterisk_channel_id)
        finally:
            await client.close()

    success, err = _run_async(_hangup())

    # Update call log regardless (Asterisk may have already hung up)
    call_log.status = CallLog.Status.COMPLETED
    call_log.ended_at = timezone.now()
    if call_log.answered_at:
        call_log.duration_seconds = int(
            (call_log.ended_at - call_log.answered_at).total_seconds()
        )
    call_log.save(update_fields=[
        "status", "ended_at", "duration_seconds", "updated_at",
    ])

    return True, None


def toggle_hold(call_log):
    """Toggle hold state on an active call."""
    if not call_log.asterisk_channel_id:
        return call_log.status, "No active ARI channel."

    client, error = _get_ari_client(call_log.tenant)
    if error:
        return None, error

    is_on_hold = call_log.status == CallLog.Status.ON_HOLD

    async def _toggle():
        try:
            if is_on_hold:
                return await client.unhold(call_log.asterisk_channel_id)
            else:
                return await client.hold(call_log.asterisk_channel_id)
        finally:
            await client.close()

    success, err = _run_async(_toggle())
    if not success:
        return None, err

    if is_on_hold:
        call_log.status = CallLog.Status.ANSWERED
    else:
        call_log.status = CallLog.Status.ON_HOLD

    call_log.save(update_fields=["status", "updated_at"])
    return call_log.status, None


def transfer_call(call_log, target_number):
    """Blind transfer an active call to another number/extension."""
    if not call_log.asterisk_channel_id:
        return False, "No active ARI channel."

    client, error = _get_ari_client(call_log.tenant)
    if error:
        return False, error

    # Determine the PJSIP endpoint for the target
    target_ext = Extension.objects.filter(
        tenant=call_log.tenant,
        extension_number=target_number,
        is_active=True,
    ).first()

    endpoint = (
        f"PJSIP/{target_ext.sip_username}" if target_ext
        else f"PJSIP/{target_number}"
    )

    async def _transfer():
        try:
            return await client.redirect(call_log.asterisk_channel_id, endpoint)
        finally:
            await client.close()

    success, err = _run_async(_transfer())
    if not success:
        return False, err

    call_log.status = CallLog.Status.COMPLETED
    call_log.ended_at = timezone.now()
    if call_log.answered_at:
        call_log.duration_seconds = int(
            (call_log.ended_at - call_log.answered_at).total_seconds()
        )
    call_log.metadata["transferred_to"] = target_number
    call_log.save(update_fields=[
        "status", "ended_at", "duration_seconds", "metadata", "updated_at",
    ])

    return True, None


# ---------------------------------------------------------------------------
# ARI event processing
# ---------------------------------------------------------------------------


def process_ari_event(event):
    """
    Process an ARI Stasis event and update the corresponding CallLog.

    Called by the ARI event listener management command.
    """
    event_type = event.get("type")
    channel = event.get("channel", {})
    channel_id = channel.get("id", "")

    if not channel_id:
        return

    # Find the call log by Asterisk channel ID
    try:
        call_log = CallLog.unscoped.get(asterisk_channel_id=channel_id)
    except CallLog.DoesNotExist:
        logger.debug("No CallLog for channel %s", channel_id)
        return

    now = timezone.now()

    if event_type == "ChannelStateChange":
        state = channel.get("state", "")
        if state == "Up" and call_log.status == CallLog.Status.RINGING:
            call_log.status = CallLog.Status.ANSWERED
            call_log.answered_at = now
            call_log.save(update_fields=["status", "answered_at", "updated_at"])
            _broadcast_call_event(call_log, "call_answered")

    elif event_type == "ChannelHangupRequest":
        call_log.status = CallLog.Status.COMPLETED
        call_log.ended_at = now
        if call_log.answered_at:
            call_log.duration_seconds = int(
                (now - call_log.answered_at).total_seconds()
            )
        else:
            # Never answered — mark as missed
            call_log.status = CallLog.Status.MISSED
        call_log.save(update_fields=[
            "status", "ended_at", "duration_seconds", "updated_at",
        ])
        _broadcast_call_event(call_log, "call_ended")

    elif event_type == "ChannelDestroyed":
        if call_log.status in (
            CallLog.Status.RINGING,
            CallLog.Status.ANSWERED,
            CallLog.Status.ON_HOLD,
        ):
            call_log.ended_at = now
            if call_log.answered_at:
                call_log.status = CallLog.Status.COMPLETED
                call_log.duration_seconds = int(
                    (now - call_log.answered_at).total_seconds()
                )
            else:
                call_log.status = CallLog.Status.MISSED
            call_log.save(update_fields=[
                "status", "ended_at", "duration_seconds", "updated_at",
            ])
            _broadcast_call_event(call_log, "call_ended")

    elif event_type == "ChannelHold":
        call_log.status = CallLog.Status.ON_HOLD
        call_log.save(update_fields=["status", "updated_at"])
        _broadcast_call_event(call_log, "call_hold")

    elif event_type == "ChannelUnhold":
        call_log.status = CallLog.Status.ANSWERED
        call_log.save(update_fields=["status", "updated_at"])
        _broadcast_call_event(call_log, "call_answered")


def _broadcast_call_event(call_log, event_type):
    """Broadcast a call event to the tenant's WebSocket group."""
    from channels.layers import get_channel_layer
    from asgiref.sync import async_to_sync

    channel_layer = get_channel_layer()
    group_name = f"voip_{call_log.tenant_id}"

    async_to_sync(channel_layer.group_send)(
        group_name,
        {
            "type": event_type,
            "call": {
                "id": str(call_log.id),
                "direction": call_log.direction,
                "status": call_log.status,
                "caller_number": call_log.caller_number,
                "callee_number": call_log.callee_number,
                "started_at": call_log.started_at.isoformat() if call_log.started_at else None,
                "answered_at": call_log.answered_at.isoformat() if call_log.answered_at else None,
                "ended_at": call_log.ended_at.isoformat() if call_log.ended_at else None,
                "duration_seconds": call_log.duration_seconds,
                "contact_id": str(call_log.contact_id) if call_log.contact_id else None,
                "ticket_id": str(call_log.ticket_id) if call_log.ticket_id else None,
            },
        },
    )
