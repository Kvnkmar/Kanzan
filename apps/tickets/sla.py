"""
SLA time calculation utilities.

Provides business-hours-aware elapsed-time computation and deadline
calculation for SLA policy enforcement.
"""

import datetime
from zoneinfo import ZoneInfo


def _get_tenant_tz(tenant_settings):
    """Return a ZoneInfo for the tenant's timezone, defaulting to UTC."""
    try:
        return ZoneInfo(tenant_settings.timezone)
    except (KeyError, Exception):
        return ZoneInfo("UTC")


def elapsed_business_minutes(start_utc, end_utc, tenant_settings):
    """
    Count minutes between *start_utc* and *end_utc* that fall within the
    tenant's configured business hours.

    Falls back to wall-clock minutes when *tenant_settings* is ``None`` or
    business hours are not properly configured.
    """
    if tenant_settings is None:
        return (end_utc - start_utc).total_seconds() / 60

    tz = _get_tenant_tz(tenant_settings)
    bh_start = tenant_settings.business_hours_start
    bh_end = tenant_settings.business_hours_end
    business_days = tenant_settings.business_days

    if not business_days or bh_start >= bh_end:
        return (end_utc - start_utc).total_seconds() / 60

    start_local = start_utc.astimezone(tz)
    end_local = end_utc.astimezone(tz)

    total_minutes = 0.0
    current = start_local

    while current < end_local:
        if current.weekday() in business_days:
            day_start = current.replace(
                hour=bh_start.hour, minute=bh_start.minute,
                second=0, microsecond=0,
            )
            day_end = current.replace(
                hour=bh_end.hour, minute=bh_end.minute,
                second=0, microsecond=0,
            )

            window_start = max(current, day_start)
            window_end = min(end_local, day_end)

            if window_start < window_end:
                total_minutes += (window_end - window_start).total_seconds() / 60

        next_day = (current + datetime.timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        current = next_day

    return total_minutes


def sla_deadline_utc(start_utc, sla_minutes, tenant_settings, business_hours_only):
    """
    Calculate the UTC datetime by which the SLA must be met.

    When *business_hours_only* is ``False``, simply adds *sla_minutes* to
    *start_utc*. Otherwise, skips non-business hours.
    """
    if not business_hours_only or tenant_settings is None:
        return start_utc + datetime.timedelta(minutes=sla_minutes)

    tz = _get_tenant_tz(tenant_settings)
    bh_start = tenant_settings.business_hours_start
    bh_end = tenant_settings.business_hours_end
    business_days = tenant_settings.business_days

    if not business_days or bh_start >= bh_end:
        return start_utc + datetime.timedelta(minutes=sla_minutes)

    remaining = float(sla_minutes)
    current = start_utc.astimezone(tz)

    for _ in range(365 * 24):
        if remaining <= 0:
            break

        if current.weekday() not in business_days:
            current = (current + datetime.timedelta(days=1)).replace(
                hour=bh_start.hour, minute=bh_start.minute,
                second=0, microsecond=0,
            )
            continue

        day_start = current.replace(
            hour=bh_start.hour, minute=bh_start.minute,
            second=0, microsecond=0,
        )
        day_end = current.replace(
            hour=bh_end.hour, minute=bh_end.minute,
            second=0, microsecond=0,
        )

        if current < day_start:
            current = day_start
        if current >= day_end:
            current = (current + datetime.timedelta(days=1)).replace(
                hour=bh_start.hour, minute=bh_start.minute,
                second=0, microsecond=0,
            )
            continue

        available = (day_end - current).total_seconds() / 60
        if remaining <= available:
            current = current + datetime.timedelta(minutes=remaining)
            remaining = 0
        else:
            remaining -= available
            current = (current + datetime.timedelta(days=1)).replace(
                hour=bh_start.hour, minute=bh_start.minute,
                second=0, microsecond=0,
            )

    return current.astimezone(ZoneInfo("UTC"))
