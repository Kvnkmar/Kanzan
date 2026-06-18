"""Custom Django deployment system checks.

Registered from ``apps.tenants.apps.TenantsConfig.ready``. These run only under
``manage.py check --deploy`` (``deploy=True``), so normal dev/test/CI commands
are unaffected -- run ``python manage.py check --deploy --fail-level ERROR`` in
the production deploy pipeline to enforce them.
"""

from django.conf import settings
from django.core.checks import Error, Tags, Warning as CheckWarning, register


@register(Tags.security, deploy=True)
def debug_must_be_off(app_configs, **kwargs):
    """Hard-fail a production deploy if DEBUG is on.

    Upgrades Django's built-in ``security.W018`` warning to a blocking error so
    a misconfigured ``DJANGO_DEBUG=True`` cannot ship to production.
    """
    errors = []
    if getattr(settings, "DEBUG", False):
        errors.append(
            Error(
                "DEBUG must be False in production.",
                hint=(
                    "Unset DJANGO_DEBUG (it fails safe to prod) or set it to "
                    "False. Never serve production traffic with DEBUG=True."
                ),
                id="kanzen.E001",
            )
        )
    return errors


@register(Tags.security, deploy=True)
def media_is_not_served_unauthenticated(app_configs, **kwargs):
    """Warn if X-Accel-Redirect isn't enabled for media in production.

    Without it Django streams every media file through Python; that still
    authorizes correctly but is slower. (The authorization itself always runs
    -- this is a performance/ops nudge, not a security hole.)
    """
    if not getattr(settings, "USE_X_ACCEL_REDIRECT", False):
        return [
            CheckWarning(
                "Media is streamed through Django (USE_X_ACCEL_REDIRECT is off).",
                hint=(
                    "In production set USE_X_ACCEL_REDIRECT=True and configure "
                    "nginx X-Accel-Redirect -- see docs/deploy/protected-media.md."
                ),
                id="kanzen.W001",
            )
        ]
    return []
