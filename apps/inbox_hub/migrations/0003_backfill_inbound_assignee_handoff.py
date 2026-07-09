from django.db import migrations


def backfill_inbound_handoff(apps, schema_editor):
    """Stamp the personal-Inbox handoff for pre-existing assigned Hub email.

    The engine auto-assign path (``AssignmentEngine.assign_to``) only started
    stamping ``InboundEmail.assignee`` / ``inbox_status`` / ``is_read`` as of
    the Triage-Lens-Removal change. HubEmails auto-assigned BEFORE that have
    ``HubEmail.assignee`` set but ``inbound.assignee IS NULL`` — and with the
    cockpit "Assigned to me" lens now gone, those rows are visible on no
    surface (the remaining lenses only show ``state=new``, and ``/inbox/``
    filters on the null ``inbound.assignee``). Hand them off so the assignee
    can find them, exactly as a fresh auto-assign now would.

    Scope: non-terminal HubEmails only — a CONVERTED_TO_TICKET row's work
    lives on the ticket and a DISMISSED row is dead, so neither belongs back
    in an inbox. Uses ``.update()`` (no ``save()``) so it can't trip
    InboundEmail's linked/actioned immutability guards, and never overwrites
    an inbound that already has an assignee.
    """
    HubEmail = apps.get_model("inbox_hub", "HubEmail")
    InboundEmail = apps.get_model("inbound_email", "InboundEmail")

    terminal_states = ("converted_to_ticket", "dismissed")
    orphaned = (
        HubEmail.objects
        .filter(assignee__isnull=False, inbound__assignee__isnull=True)
        .exclude(state__in=terminal_states)
        .values_list("inbound_id", "assignee_id")
    )
    for inbound_id, assignee_id in orphaned:
        if inbound_id is None:
            continue
        InboundEmail.objects.filter(pk=inbound_id).update(
            assignee_id=assignee_id,
            inbox_status="pending",
            is_read=False,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("inbound_email", "0010_inboundemail_assignee_and_more"),
        ("inbox_hub", "0002_active_sla_index_covers_escalated"),
    ]

    operations = [
        # Forward-only: the reverse can't distinguish a backfilled handoff from
        # a genuine one, so un-stamping would risk hiding legitimately-assigned
        # mail. Mirrors the noop-reverse pattern of tenants/0011, crm/0005.
        migrations.RunPython(backfill_inbound_handoff, migrations.RunPython.noop),
    ]
