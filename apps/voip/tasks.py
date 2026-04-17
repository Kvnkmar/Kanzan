"""
Celery tasks for VoIP operations.

Handles asynchronous recording processing, call state cleanup,
and ARI event dispatch.
"""

import logging

from celery import shared_task
from django.core.files.base import ContentFile
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def process_call_recording(self, call_log_id):
    """
    Download a call recording from Asterisk and store it locally.

    Called after a recorded call completes. Downloads the audio file
    from ARI, creates a CallRecording record, and stores it in the
    tenant-isolated media path.
    """
    from apps.voip.models import CallLog, CallRecording
    from apps.voip.services import _get_ari_client, _run_async

    try:
        call_log = CallLog.unscoped.select_related("tenant").get(id=call_log_id)
    except CallLog.DoesNotExist:
        logger.error("CallLog %s not found for recording processing", call_log_id)
        return

    # Skip if recording already exists
    if hasattr(call_log, "recording"):
        logger.info("Recording already exists for CallLog %s", call_log_id)
        return

    client, error = _get_ari_client(call_log.tenant)
    if error:
        logger.error("Cannot get ARI client for recording: %s", error)
        raise self.retry(exc=Exception(error))

    recording_name = f"call-{call_log_id}"

    async def _download():
        try:
            return await client.get_recording_file(recording_name)
        finally:
            await client.close()

    content, err = _run_async(_download())
    if err:
        logger.error("Failed to download recording %s: %s", recording_name, err)
        raise self.retry(exc=Exception(err))

    if not content:
        logger.warning("Empty recording for CallLog %s", call_log_id)
        return

    recording = CallRecording(
        tenant=call_log.tenant,
        call_log=call_log,
        duration_seconds=call_log.duration_seconds,
        size_bytes=len(content),
        mime_type="audio/wav",
    )
    recording.file.save(
        f"call-{call_log_id}.wav",
        ContentFile(content),
        save=False,
    )
    recording.save()

    logger.info(
        "Stored recording for CallLog %s (%d bytes)",
        call_log_id,
        len(content),
    )


@shared_task
def cleanup_stale_calls():
    """
    Mark calls stuck in active states for >2 hours as failed.

    Runs periodically via Celery Beat to clean up orphaned call records
    where ARI events were missed.
    """
    from apps.voip.models import CallLog

    cutoff = timezone.now() - timezone.timedelta(hours=2)
    stale_calls = CallLog.unscoped.filter(
        status__in=[
            CallLog.Status.RINGING,
            CallLog.Status.ANSWERED,
            CallLog.Status.ON_HOLD,
        ],
        started_at__lt=cutoff,
    )

    count = stale_calls.update(
        status=CallLog.Status.FAILED,
        ended_at=timezone.now(),
    )

    if count:
        logger.warning("Cleaned up %d stale calls", count)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def sync_call_state(self, channel_id, event_data):
    """
    Process an ARI event asynchronously.

    Called by the ARI listener to offload event processing to Celery,
    preventing the listener from blocking on database operations.
    """
    from apps.voip.services import process_ari_event

    try:
        process_ari_event(event_data)
    except Exception as e:
        logger.error("Failed to process ARI event for %s: %s", channel_id, e)
        raise self.retry(exc=e)
