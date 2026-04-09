"""
SLA time calculation utilities.

Provides business-hours-aware elapsed-time computation and deadline
calculation for SLA policy enforcement.  Supports:

- Per-day open/close times via the ``BusinessHours`` model
- Public holidays via the ``PublicHoliday`` model
- SLA pause periods via the ``SLAPause`` model
- Backward compatibility with ``TenantSettings`` flat fields when no
  ``BusinessHours`` row exists for a tenant

All datetime arithmetic is timezone-aware.  Datetimes are converted to
the tenant's local timezone for business-hours windowing, then results
are returned in UTC.
"""

import datetime
from zoneinfo import ZoneInfo


# ---------------------------------------------------------------------------
# Timezone helper
# ---------------------------------------------------------------------------


def _get_tz(tz_name):
    """Return a ZoneInfo for *tz_name*, defaulting to UTC."""
    try:
        return ZoneInfo(tz_name)
    except (KeyError, Exception):
        return ZoneInfo("UTC")


# ---------------------------------------------------------------------------
# Business hours configuration resolution
# ---------------------------------------------------------------------------


def _get_business_config(tenant):
    """
    Return a normalised business-hours configuration for *tenant*.

    Tries the ``BusinessHours`` model first, then falls back to the flat
    fields on ``TenantSettings``.  Returns ``None`` when neither is
    configured (meaning 24/7 mode).

    Return shape (when not None)::

        {
            "tz": ZoneInfo,
            "days": {
                0: (open_time, close_time),   # Monday
                ...
            },
            "holiday_dates": set[datetime.date],
        }
    """
    from apps.tickets.models import BusinessHours, PublicHoliday

    try:
        bh = BusinessHours.unscoped.select_related("tenant").get(tenant=tenant)
    except BusinessHours.DoesNotExist:
        bh = None

    if bh is not None:
        tz = _get_tz(bh.timezone)
        days = {}
        for weekday in range(7):
            is_active, open_t, close_t = bh.get_day_config(weekday)
            if is_active and open_t and close_t and open_t < close_t:
                days[weekday] = (open_t, close_t)
        if not days:
            return None  # No active days → 24/7 fallback

        holidays = set(
            PublicHoliday.unscoped.filter(tenant=tenant)
            .values_list("date", flat=True)
        )
        return {"tz": tz, "days": days, "holiday_dates": holidays}

    # Fallback to TenantSettings flat fields
    tenant_settings = getattr(tenant, "settings", None)
    if tenant_settings is None:
        return None

    bh_start = tenant_settings.business_hours_start
    bh_end = tenant_settings.business_hours_end
    business_days = tenant_settings.business_days

    if not business_days or bh_start >= bh_end:
        return None

    tz = _get_tz(tenant_settings.timezone)
    days = {d: (bh_start, bh_end) for d in business_days}
    holidays = set(
        PublicHoliday.unscoped.filter(tenant=tenant)
        .values_list("date", flat=True)
    )
    return {"tz": tz, "days": days, "holiday_dates": holidays}


# ---------------------------------------------------------------------------
# Core business-hours functions
# ---------------------------------------------------------------------------


def is_within_business_hours(dt, tenant):
    """Return True if *dt* falls within the tenant's business hours."""
    config = _get_business_config(tenant)
    if config is None:
        return True  # 24/7 mode

    local_dt = dt.astimezone(config["tz"])
    if local_dt.date() in config["holiday_dates"]:
        return False

    day_config = config["days"].get(local_dt.weekday())
    if day_config is None:
        return False

    open_t, close_t = day_config
    current_time = local_dt.time()
    return open_t <= current_time < close_t


def elapsed_business_minutes(start_utc, end_utc, tenant_or_settings):
    """
    Count minutes between *start_utc* and *end_utc* that fall within the
    tenant's configured business hours, skipping holidays.

    *tenant_or_settings* can be a ``Tenant`` instance or a
    ``TenantSettings`` instance (for backward compatibility with the
    existing breach-detection code).

    Returns wall-clock minutes when business hours are not configured.
    """
    # Resolve tenant object
    tenant = _resolve_tenant(tenant_or_settings)
    if tenant is None:
        return (end_utc - start_utc).total_seconds() / 60

    config = _get_business_config(tenant)
    if config is None:
        return (end_utc - start_utc).total_seconds() / 60

    return _count_business_minutes(start_utc, end_utc, config)


def add_business_minutes(start_utc, minutes, tenant):
    """
    Return the UTC datetime that is *minutes* business-minutes after
    *start_utc*, skipping nights, weekends, and holidays.

    Used to compute ``response_due_at`` and ``resolution_due_at`` at
    ticket creation.
    """
    config = _get_business_config(tenant)
    if config is None:
        return start_utc + datetime.timedelta(minutes=minutes)

    return _add_business_minutes(start_utc, minutes, config)


def get_business_minutes_elapsed(start_utc, end_utc, tenant):
    """Alias for ``elapsed_business_minutes`` using a Tenant instance."""
    return elapsed_business_minutes(start_utc, end_utc, tenant)


# ---------------------------------------------------------------------------
# SLA pause support
# ---------------------------------------------------------------------------


def get_total_pause_minutes(ticket, start_utc=None, end_utc=None, config=None):
    """
    Sum the business-hours-adjusted pause duration for all ``SLAPause``
    records on *ticket*.

    When *config* is provided, pause durations are counted in business
    minutes only.  Otherwise raw wall-clock minutes are returned.
    """
    from apps.tickets.models import SLAPause

    pauses = SLAPause.unscoped.filter(ticket=ticket)
    if start_utc:
        pauses = pauses.filter(paused_at__gte=start_utc)

    total = 0.0
    now = end_utc or datetime.datetime.now(datetime.timezone.utc)

    for pause in pauses:
        pause_start = pause.paused_at
        pause_end = pause.resumed_at or now

        if config is not None:
            total += _count_business_minutes(pause_start, pause_end, config)
        else:
            total += (pause_end - pause_start).total_seconds() / 60

    return total


def get_effective_elapsed_minutes(ticket, policy, tenant, now=None):
    """
    Calculate the effective elapsed time for a ticket, subtracting
    paused periods and applying business hours filtering.

    This is the single entry point for breach detection.

    Returns elapsed minutes as a float.
    """
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)

    config = None
    if policy.business_hours_only:
        config = _get_business_config(tenant)

    # Raw or business-hours elapsed time
    if config is not None:
        raw_elapsed = _count_business_minutes(ticket.created_at, now, config)
        pause_minutes = get_total_pause_minutes(
            ticket, start_utc=ticket.created_at, end_utc=now, config=config
        )
    else:
        raw_elapsed = (now - ticket.created_at).total_seconds() / 60
        pause_minutes = get_total_pause_minutes(
            ticket, start_utc=ticket.created_at, end_utc=now, config=None
        )

    return max(0.0, raw_elapsed - pause_minutes)


# ---------------------------------------------------------------------------
# Backward-compatible wrappers
# ---------------------------------------------------------------------------


def sla_deadline_utc(start_utc, sla_minutes, tenant_settings, business_hours_only):
    """
    Calculate the UTC datetime by which the SLA must be met.

    Backward-compatible wrapper that accepts ``TenantSettings``.
    """
    if not business_hours_only:
        return start_utc + datetime.timedelta(minutes=sla_minutes)

    tenant = _resolve_tenant(tenant_settings)
    if tenant is None:
        return start_utc + datetime.timedelta(minutes=sla_minutes)

    config = _get_business_config(tenant)
    if config is None:
        return start_utc + datetime.timedelta(minutes=sla_minutes)

    return _add_business_minutes(start_utc, sla_minutes, config)


# ---------------------------------------------------------------------------
# Internal calculation helpers
# ---------------------------------------------------------------------------


def _resolve_tenant(tenant_or_settings):
    """Resolve a Tenant instance from either a Tenant or TenantSettings."""
    if tenant_or_settings is None:
        return None
    # If it's a TenantSettings, get the tenant
    if hasattr(tenant_or_settings, "tenant"):
        return tenant_or_settings.tenant
    # If it has a 'settings' attribute, it's already a Tenant
    if hasattr(tenant_or_settings, "settings"):
        return tenant_or_settings
    return None


def _count_business_minutes(start_utc, end_utc, config):
    """
    Count business minutes between two UTC datetimes using *config*.

    Skips non-business days, holidays, and hours outside the configured
    open/close window for each day.
    """
    tz = config["tz"]
    days = config["days"]
    holidays = config["holiday_dates"]

    start_local = start_utc.astimezone(tz)
    end_local = end_utc.astimezone(tz)

    if start_local >= end_local:
        return 0.0

    total_minutes = 0.0
    current = start_local

    while current < end_local:
        current_date = current.date()

        # Skip holidays
        if current_date in holidays:
            current = (current + datetime.timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0,
            )
            continue

        day_config = days.get(current.weekday())
        if day_config is None:
            # Non-business day
            current = (current + datetime.timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0,
            )
            continue

        open_t, close_t = day_config
        day_start = current.replace(
            hour=open_t.hour, minute=open_t.minute,
            second=0, microsecond=0,
        )
        day_end = current.replace(
            hour=close_t.hour, minute=close_t.minute,
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


def _add_business_minutes(start_utc, minutes, config):
    """
    Return the UTC datetime that is *minutes* business-minutes after
    *start_utc*, skipping non-business hours, weekends, and holidays.
    """
    tz = config["tz"]
    days = config["days"]
    holidays = config["holiday_dates"]

    remaining = float(minutes)
    current = start_utc.astimezone(tz)

    # Safety limit: 365 days worth of iterations
    for _ in range(365 * 24):
        if remaining <= 0:
            break

        current_date = current.date()

        # Skip holidays
        if current_date in holidays:
            # Jump to next day's earliest business hour
            current = _next_business_day_start(current, days, holidays)
            continue

        day_config = days.get(current.weekday())
        if day_config is None:
            current = _next_business_day_start(current, days, holidays)
            continue

        open_t, close_t = day_config
        day_start = current.replace(
            hour=open_t.hour, minute=open_t.minute,
            second=0, microsecond=0,
        )
        day_end = current.replace(
            hour=close_t.hour, minute=close_t.minute,
            second=0, microsecond=0,
        )

        if current < day_start:
            current = day_start
        if current >= day_end:
            current = _next_business_day_start(current, days, holidays)
            continue

        available = (day_end - current).total_seconds() / 60
        if remaining <= available:
            current = current + datetime.timedelta(minutes=remaining)
            remaining = 0
        else:
            remaining -= available
            current = _next_business_day_start(current, days, holidays)

    return current.astimezone(ZoneInfo("UTC"))


def _next_business_day_start(current, days, holidays):
    """Advance to the start of the next business day."""
    candidate = (current + datetime.timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    for _ in range(365):
        if candidate.date() not in holidays:
            day_config = days.get(candidate.weekday())
            if day_config is not None:
                open_t, _ = day_config
                return candidate.replace(
                    hour=open_t.hour, minute=open_t.minute,
                    second=0, microsecond=0,
                )
        candidate = candidate + datetime.timedelta(days=1)
    return candidate  # Should never reach here
