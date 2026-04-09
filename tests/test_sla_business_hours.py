"""
Tests for SLA business hours utilities.

Covers:
- Overnight span
- Weekend crossing
- Holiday skipping
- Timezone offset handling
- Zero-business-hours edge case
- add_business_minutes crossing day boundaries
- Backward compatibility with TenantSettings fallback
- SLA pause integration
"""

import datetime

import pytest
from django.utils import timezone
from zoneinfo import ZoneInfo

from apps.tickets.models import BusinessHours, PublicHoliday, SLAPause
from apps.tickets.sla import (
    add_business_minutes,
    elapsed_business_minutes,
    get_business_minutes_elapsed,
    get_effective_elapsed_minutes,
    is_within_business_hours,
)
from conftest import (
    TenantFactory,
    TicketFactory,
    TicketStatusFactory,
    UserFactory,
)
from main.context import clear_current_tenant, set_current_tenant


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bh(tenant, tz="UTC", schedule=None):
    """Create a BusinessHours with a standard Mon-Fri 09:00–17:00 schedule."""
    if schedule is None:
        schedule = {}
        for day in range(7):
            schedule[str(day)] = {
                "is_active": day < 5,
                "open_time": "09:00",
                "close_time": "17:00",
            }
    return BusinessHours.unscoped.create(
        tenant=tenant,
        timezone=tz,
        schedule=schedule,
    )


def _utc(*args):
    """Shorthand to create a UTC datetime."""
    return datetime.datetime(*args, tzinfo=ZoneInfo("UTC"))


# ---------------------------------------------------------------------------
# Tests: is_within_business_hours
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestIsWithinBusinessHours:
    def test_no_config_returns_true(self, tenant):
        """24/7 fallback when no BusinessHours and no TenantSettings bh config."""
        # Clear TenantSettings business hours to trigger 24/7 fallback
        settings = tenant.settings
        settings.business_days = []
        settings.save()
        dt = _utc(2026, 3, 25, 3, 0)  # Wednesday 3am UTC
        assert is_within_business_hours(dt, tenant) is True

    def test_during_business_hours(self, tenant):
        _make_bh(tenant)
        dt = _utc(2026, 3, 25, 10, 0)  # Wednesday 10am UTC
        assert is_within_business_hours(dt, tenant) is True

    def test_outside_business_hours(self, tenant):
        _make_bh(tenant)
        dt = _utc(2026, 3, 25, 20, 0)  # Wednesday 8pm UTC
        assert is_within_business_hours(dt, tenant) is False

    def test_weekend(self, tenant):
        _make_bh(tenant)
        dt = _utc(2026, 3, 28, 10, 0)  # Saturday
        assert is_within_business_hours(dt, tenant) is False

    def test_holiday(self, tenant):
        set_current_tenant(tenant)
        _make_bh(tenant)
        PublicHoliday.objects.create(
            tenant=tenant,
            date=datetime.date(2026, 3, 25),
            name="Test Holiday",
        )
        dt = _utc(2026, 3, 25, 10, 0)  # Wednesday but holiday
        assert is_within_business_hours(dt, tenant) is False
        clear_current_tenant()

    def test_timezone_offset(self, tenant):
        """Business hours in US/Eastern: 9am-5pm ET = 14:00-22:00 UTC."""
        _make_bh(tenant, tz="America/New_York")
        # 13:00 UTC = 9:00 AM ET (during EDT, March 25 2026)
        # Actually EDT starts March 8 2026, so March 25 is EDT (-4)
        # 13:00 UTC = 9:00 AM EDT → should be within hours
        dt = _utc(2026, 3, 25, 13, 0)
        assert is_within_business_hours(dt, tenant) is True

        # 12:00 UTC = 8:00 AM EDT → before business hours
        dt2 = _utc(2026, 3, 25, 12, 0)
        assert is_within_business_hours(dt2, tenant) is False


# ---------------------------------------------------------------------------
# Tests: elapsed_business_minutes
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestElapsedBusinessMinutes:
    def test_no_config_returns_wallclock(self, tenant):
        """Fallback to wall-clock minutes."""
        start = _utc(2026, 3, 25, 10, 0)
        end = _utc(2026, 3, 25, 12, 30)
        result = elapsed_business_minutes(start, end, tenant)
        assert result == 150.0

    def test_within_single_day(self, tenant):
        _make_bh(tenant)
        start = _utc(2026, 3, 25, 10, 0)  # Wed 10am
        end = _utc(2026, 3, 25, 14, 0)    # Wed 2pm
        result = elapsed_business_minutes(start, end, tenant)
        assert result == 240.0  # 4 hours

    def test_overnight_span(self, tenant):
        """Span from 4pm Wednesday to 10am Thursday → only business hours count."""
        _make_bh(tenant)
        start = _utc(2026, 3, 25, 16, 0)  # Wed 4pm
        end = _utc(2026, 3, 26, 10, 0)    # Thu 10am
        result = elapsed_business_minutes(start, end, tenant)
        # Wed 16:00-17:00 = 60 min + Thu 09:00-10:00 = 60 min = 120
        assert result == 120.0

    def test_weekend_crossing(self, tenant):
        """Friday 4pm to Monday 10am — only Fri 4-5pm + Mon 9-10am count."""
        _make_bh(tenant)
        start = _utc(2026, 3, 27, 16, 0)  # Fri 4pm
        end = _utc(2026, 3, 30, 10, 0)    # Mon 10am
        result = elapsed_business_minutes(start, end, tenant)
        # Fri 16:00-17:00 = 60 + Mon 09:00-10:00 = 60 = 120
        assert result == 120.0

    def test_holiday_skipped(self, tenant):
        """Holiday Wednesday — business minutes only count Thu."""
        set_current_tenant(tenant)
        _make_bh(tenant)
        PublicHoliday.objects.create(
            tenant=tenant,
            date=datetime.date(2026, 3, 25),
            name="Holiday Wed",
        )
        start = _utc(2026, 3, 25, 10, 0)  # Wed (holiday)
        end = _utc(2026, 3, 26, 10, 0)    # Thu 10am
        result = elapsed_business_minutes(start, end, tenant)
        # Wed entirely skipped, Thu 09:00-10:00 = 60
        assert result == 60.0
        clear_current_tenant()

    def test_timezone_offset_elapsed(self, tenant):
        """Business hours in Asia/Tokyo (UTC+9): 09:00-17:00 JST."""
        _make_bh(tenant, tz="Asia/Tokyo")
        # 00:00 UTC = 09:00 JST (business start)
        # 08:00 UTC = 17:00 JST (business end)
        start = _utc(2026, 3, 25, 0, 0)
        end = _utc(2026, 3, 25, 8, 0)
        result = elapsed_business_minutes(start, end, tenant)
        assert result == 480.0  # Full 8-hour day

    def test_zero_business_hours(self, tenant):
        """All days inactive → falls back to 24/7 (returns None config)."""
        schedule = {}
        for day in range(7):
            schedule[str(day)] = {
                "is_active": False,
                "open_time": "09:00",
                "close_time": "17:00",
            }
        _make_bh(tenant, schedule=schedule)
        start = _utc(2026, 3, 25, 10, 0)
        end = _utc(2026, 3, 25, 12, 0)
        result = elapsed_business_minutes(start, end, tenant)
        # No active days → config is None → wall-clock fallback
        assert result == 120.0

    def test_backward_compat_with_tenant_settings(self, tenant):
        """When no BusinessHours exists, falls back to TenantSettings fields."""
        settings = tenant.settings
        settings.business_hours_start = datetime.time(9, 0)
        settings.business_hours_end = datetime.time(17, 0)
        settings.business_days = [0, 1, 2, 3, 4]
        settings.timezone = "UTC"
        settings.save()

        start = _utc(2026, 3, 25, 10, 0)  # Wed 10am
        end = _utc(2026, 3, 25, 14, 0)    # Wed 2pm
        result = elapsed_business_minutes(start, end, tenant)
        assert result == 240.0


# ---------------------------------------------------------------------------
# Tests: add_business_minutes
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAddBusinessMinutes:
    def test_no_config_adds_raw(self, tenant):
        start = _utc(2026, 3, 25, 10, 0)
        result = add_business_minutes(start, 120, tenant)
        assert result == _utc(2026, 3, 25, 12, 0)

    def test_within_single_day(self, tenant):
        _make_bh(tenant)
        start = _utc(2026, 3, 25, 10, 0)  # Wed 10am
        result = add_business_minutes(start, 120, tenant)  # +2 hours
        assert result == _utc(2026, 3, 25, 12, 0)

    def test_crossing_day_boundary(self, tenant):
        """Start at 4pm Wed, add 120 min → should land at 10am Thu."""
        _make_bh(tenant)
        start = _utc(2026, 3, 25, 16, 0)  # Wed 4pm
        result = add_business_minutes(start, 120, tenant)
        # Wed 16:00-17:00 = 60 min consumed, 60 remaining
        # Thu 09:00 + 60 min = 10:00
        assert result == _utc(2026, 3, 26, 10, 0)

    def test_crossing_weekend(self, tenant):
        """Start Fri 4pm, add 120 min → Fri 4-5pm (60) + Mon 9-10am (60)."""
        _make_bh(tenant)
        start = _utc(2026, 3, 27, 16, 0)  # Fri 4pm
        result = add_business_minutes(start, 120, tenant)
        assert result == _utc(2026, 3, 30, 10, 0)  # Mon 10am

    def test_crossing_holiday(self, tenant):
        """Holiday on Thursday — should skip to Friday."""
        set_current_tenant(tenant)
        _make_bh(tenant)
        PublicHoliday.objects.create(
            tenant=tenant,
            date=datetime.date(2026, 3, 26),
            name="Holiday Thu",
        )
        start = _utc(2026, 3, 25, 16, 0)  # Wed 4pm
        result = add_business_minutes(start, 120, tenant)
        # Wed 16-17 = 60 used, Thu is holiday, Fri 09:00 + 60 = 10:00
        assert result == _utc(2026, 3, 27, 10, 0)
        clear_current_tenant()

    def test_multi_day_span(self, tenant):
        """Add a full business week (40 hours = 2400 min)."""
        _make_bh(tenant)
        start = _utc(2026, 3, 23, 9, 0)  # Mon 9am
        result = add_business_minutes(start, 2400, tenant)
        # 5 days * 480 min = 2400 → Fri 17:00
        assert result == _utc(2026, 3, 27, 17, 0)

    def test_start_before_business_hours(self, tenant):
        """Start at 6am → should snap to 9am."""
        _make_bh(tenant)
        start = _utc(2026, 3, 25, 6, 0)  # Wed 6am
        result = add_business_minutes(start, 60, tenant)
        assert result == _utc(2026, 3, 25, 10, 0)  # Wed 10am

    def test_start_after_business_hours(self, tenant):
        """Start at 8pm Wed → should snap to next day 9am."""
        _make_bh(tenant)
        start = _utc(2026, 3, 25, 20, 0)  # Wed 8pm
        result = add_business_minutes(start, 60, tenant)
        assert result == _utc(2026, 3, 26, 10, 0)  # Thu 10am


# ---------------------------------------------------------------------------
# Tests: get_effective_elapsed_minutes (with pause support)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEffectiveElapsedMinutes:
    def test_no_pauses(self, tenant):
        """Without pauses, effective elapsed = raw elapsed."""
        set_current_tenant(tenant)
        from apps.tickets.models import SLAPolicy, Ticket

        status = TicketStatusFactory(tenant=tenant, is_default=True)
        user = UserFactory()
        ticket = TicketFactory(tenant=tenant, status=status, created_by=user)
        # Force created_at (auto_now_add overrides factory value)
        Ticket.unscoped.filter(pk=ticket.pk).update(created_at=_utc(2026, 3, 25, 10, 0))
        ticket.refresh_from_db()

        policy = SLAPolicy.unscoped.create(
            tenant=tenant,
            name="Test",
            priority="medium",
            first_response_minutes=60,
            resolution_minutes=480,
            business_hours_only=False,
        )
        now = _utc(2026, 3, 25, 12, 0)  # 2 hours later
        result = get_effective_elapsed_minutes(ticket, policy, tenant, now)
        assert result == 120.0
        clear_current_tenant()

    def test_with_pauses_subtracts_pause_time(self, tenant):
        """Pause from 10:30 to 11:00 should subtract 30 min."""
        set_current_tenant(tenant)
        from apps.tickets.models import SLAPolicy, Ticket

        status = TicketStatusFactory(tenant=tenant, is_default=True)
        user = UserFactory()
        ticket = TicketFactory(tenant=tenant, status=status, created_by=user)
        Ticket.unscoped.filter(pk=ticket.pk).update(created_at=_utc(2026, 3, 25, 10, 0))
        ticket.refresh_from_db()

        policy = SLAPolicy.unscoped.create(
            tenant=tenant,
            name="Test",
            priority="medium",
            first_response_minutes=60,
            resolution_minutes=480,
            business_hours_only=False,
        )
        SLAPause.unscoped.create(
            tenant=tenant,
            ticket=ticket,
            paused_at=_utc(2026, 3, 25, 10, 30),
            resumed_at=_utc(2026, 3, 25, 11, 0),
            reason="waiting_on_customer",
        )
        now = _utc(2026, 3, 25, 12, 0)
        result = get_effective_elapsed_minutes(ticket, policy, tenant, now)
        # Raw = 120 min, pause = 30 min, effective = 90
        assert result == 90.0
        clear_current_tenant()

    def test_with_business_hours_and_pauses(self, tenant):
        """Business hours + pause: only business-hour portions count."""
        set_current_tenant(tenant)
        _make_bh(tenant)
        from apps.tickets.models import SLAPolicy, Ticket

        status = TicketStatusFactory(tenant=tenant, is_default=True)
        user = UserFactory()
        ticket = TicketFactory(tenant=tenant, status=status, created_by=user)
        Ticket.unscoped.filter(pk=ticket.pk).update(created_at=_utc(2026, 3, 25, 10, 0))
        ticket.refresh_from_db()

        policy = SLAPolicy.unscoped.create(
            tenant=tenant,
            name="Test",
            priority="medium",
            first_response_minutes=60,
            resolution_minutes=480,
            business_hours_only=True,
        )
        # Pause during business hours: 14:00-15:00
        SLAPause.unscoped.create(
            tenant=tenant,
            ticket=ticket,
            paused_at=_utc(2026, 3, 25, 14, 0),
            resumed_at=_utc(2026, 3, 25, 15, 0),
            reason="waiting_on_customer",
        )
        now = _utc(2026, 3, 25, 17, 0)
        result = get_effective_elapsed_minutes(ticket, policy, tenant, now)
        # Business elapsed: 10:00-17:00 = 420 min
        # Pause business elapsed: 14:00-15:00 = 60 min
        # Effective = 420 - 60 = 360
        assert result == 360.0
        clear_current_tenant()

    def test_open_pause_uses_now(self, tenant):
        """An open pause (no resumed_at) should use now as end."""
        set_current_tenant(tenant)
        from apps.tickets.models import SLAPolicy, Ticket

        status = TicketStatusFactory(tenant=tenant, is_default=True)
        user = UserFactory()
        ticket = TicketFactory(tenant=tenant, status=status, created_by=user)
        Ticket.unscoped.filter(pk=ticket.pk).update(created_at=_utc(2026, 3, 25, 10, 0))
        ticket.refresh_from_db()

        policy = SLAPolicy.unscoped.create(
            tenant=tenant,
            name="Test",
            priority="medium",
            first_response_minutes=60,
            resolution_minutes=480,
            business_hours_only=False,
        )
        SLAPause.unscoped.create(
            tenant=tenant,
            ticket=ticket,
            paused_at=_utc(2026, 3, 25, 11, 0),
            resumed_at=None,
            reason="waiting_on_customer",
        )
        now = _utc(2026, 3, 25, 12, 0)
        result = get_effective_elapsed_minutes(ticket, policy, tenant, now)
        # Raw = 120 min, pause = 60 min (11:00 to now=12:00), effective = 60
        assert result == 60.0
        clear_current_tenant()
