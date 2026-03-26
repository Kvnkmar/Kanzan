"""
Data migration: backfill direction, sender_type, and idempotency_key
for existing InboundEmail records.

Outbound records (created by _record_outbound_message_id) are identified
by having a non-null ticket, status='reply_added', and an empty
in_reply_to field (outbound records never set threading headers).

All records receive an idempotency_key based on their direction, tenant,
and message_id to enable the DB-level unique constraint for dedup.
"""

from django.db import migrations


def backfill_direction_and_keys(apps, schema_editor):
    """
    Backfill existing records in batches.

    1. Mark outbound records: records that were created by the outbound
       email service have status='reply_added', a linked ticket, and no
       in_reply_to header (real inbound replies almost always have one).
       We also check for empty raw_headers since outbound tracking records
       never store headers.

    2. Generate idempotency_key for all records.
    """
    InboundEmail = apps.get_model("inbound_email", "InboundEmail")

    # Step 1: Identify and mark outbound records.
    # These are synthetic records from _record_outbound_message_id():
    # - They have a ticket linked (ticket is not null)
    # - status is 'reply_added' (set explicitly by the recording function)
    # - raw_headers is empty (webhook-received emails always have headers)
    # - in_reply_to is empty (outbound records never set this)
    outbound_count = InboundEmail.objects.filter(
        ticket__isnull=False,
        status="reply_added",
        raw_headers="",
        in_reply_to="",
    ).update(
        direction="outbound",
        sender_type="system",
    )

    # Step 2: Backfill idempotency_key for all records.
    # Process in batches to avoid loading entire table into memory.
    batch_size = 500
    records = InboundEmail.objects.filter(
        idempotency_key__isnull=True,
    ).only("id", "direction", "tenant_id", "ticket_id", "message_id")

    batch = []
    for record in records.iterator(chunk_size=batch_size):
        if record.direction == "outbound" and record.ticket_id:
            key = f"out:{record.tenant_id}:{record.ticket_id}:{record.message_id}"
        elif record.tenant_id:
            key = f"in:{record.tenant_id}:{record.message_id}"
        else:
            # Records without a tenant (rejected before resolution)
            key = f"in:none:{record.id}"

        record.idempotency_key = key
        batch.append(record)

        if len(batch) >= batch_size:
            InboundEmail.objects.bulk_update(batch, ["idempotency_key"])
            batch = []

    if batch:
        InboundEmail.objects.bulk_update(batch, ["idempotency_key"])


def reverse_backfill(apps, schema_editor):
    """
    Reverse: reset direction/sender_type to defaults and clear idempotency_key.
    """
    InboundEmail = apps.get_model("inbound_email", "InboundEmail")
    InboundEmail.objects.filter(direction="outbound").update(
        direction="inbound",
        sender_type="customer",
    )
    InboundEmail.objects.exclude(idempotency_key__isnull=True).update(
        idempotency_key=None,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("inbound_email", "0003_add_direction_sender_type_idempotency"),
    ]

    operations = [
        migrations.RunPython(
            backfill_direction_and_keys,
            reverse_code=reverse_backfill,
        ),
    ]
