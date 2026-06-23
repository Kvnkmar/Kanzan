# Generated for "agent-decides ticket confirmation email" change.

from django.db import migrations, models


def disable_auto_send(apps, schema_editor):
    """
    Flip existing tenants to agent-decides.

    The field previously defaulted to True, so any tenant still holding that
    value just inherited the old default rather than deliberately opting in.
    Set those rows to False so the confirmation email is sent only when an
    agent clicks "Send confirmation email" on the ticket. Tenants can re-enable
    auto-send from Settings at any time.
    """
    TenantSettings = apps.get_model("tenants", "TenantSettings")
    TenantSettings.objects.filter(auto_send_ticket_created_email=True).update(
        auto_send_ticket_created_email=False
    )


def noop(apps, schema_editor):
    # Forward-only: do not silently re-enable auto-send on reverse.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0010_tenantsettings_inbox_hub_auto_assign_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='tenantsettings',
            name='auto_send_ticket_created_email',
            field=models.BooleanField(
                default=False,
                help_text=(
                    "When True, the ticket-created confirmation email is sent to the "
                    "contact automatically the moment a ticket is created from an "
                    "inbound email. When False (the default), agents decide per ticket "
                    "and send it manually from the ticket page (button: 'Send "
                    "confirmation email')."
                ),
            ),
        ),
        migrations.RunPython(disable_auto_send, noop),
    ]
