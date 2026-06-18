"""Backfill VoIP entitlement flags on existing Plan rows.

The original ``seed_plans`` command never set ``has_voip`` /
``has_call_recording`` / ``max_calls_per_month``, so every seeded plan fell to
the model defaults (VoIP off, NULL cap) and ``check_call_limit`` denied calls
for all tenants -- including Enterprise. This data migration aligns existing
rows with the corrected seeder. Forward-only intent; reverse is a no-op so the
migration can be unapplied without clobbering operator customisations.
"""

from django.db import migrations

# (tier, has_voip, has_call_recording, max_calls_per_month)
TIER_VOIP = [
    ("free", False, False, 0),
    ("pro", True, True, 1000),
    ("enterprise", True, True, None),
]


def backfill_voip_flags(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    for tier, has_voip, has_recording, max_calls in TIER_VOIP:
        Plan.objects.filter(tier=tier).update(
            has_voip=has_voip,
            has_call_recording=has_recording,
            max_calls_per_month=max_calls,
        )


def noop_reverse(apps, schema_editor):
    # Intentionally do nothing on reverse: we do not want to re-disable VoIP
    # for plans an operator may have since customised.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0002_plan_has_call_recording_plan_has_voip_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_voip_flags, noop_reverse),
    ]
