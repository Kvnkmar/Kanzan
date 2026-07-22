"""
Tests for the Overdue Reminders feature.

Covers:
    - Overdue detection logic
    - Tenant isolation
    - API endpoints (list, overdue, complete, cancel, reschedule, bulk, stats)
    - Permission / RBAC
    - Dashboard integration
    - Celery task for overdue notifications
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from conftest import (
    ContactFactory,
    MembershipFactory,
    ReminderFactory,
    SubscriptionFactory,
    TenantFactory,
    UserFactory,
)
from main.context import clear_current_tenant, set_current_tenant


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_reminder(tenant, user, **kwargs):
    """Create a reminder with tenant context set.

    The legacy ``contact`` / ``ticket`` keyword arguments are accepted for
    convenience — they map onto the new ManyToMany ``contacts`` / ``tickets``
    relations so existing tests keep working without rewriting every call.
    """
    set_current_tenant(tenant)
    contact = kwargs.pop("contact", None)
    contacts = kwargs.pop("contacts", None)
    if contact and not contacts:
        contacts = [contact]
    ticket = kwargs.pop("ticket", None)
    tickets = kwargs.pop("tickets", None)
    if ticket and not tickets:
        tickets = [ticket]
    reminder = ReminderFactory(tenant=tenant, created_by=user, **kwargs)
    if contacts:
        reminder.contacts.set(contacts)
    if tickets:
        reminder.tickets.set(tickets)
    clear_current_tenant()
    return reminder


# ---------------------------------------------------------------------------
# 1. Pending reminder before due time -> NOT overdue
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestReminderOverdueLogic:

    def test_pending_before_due_not_overdue(self, tenant, admin_user):
        """A reminder scheduled in the future should not be overdue."""
        reminder = _create_reminder(
            tenant, admin_user,
            scheduled_at=timezone.now() + timedelta(hours=2),
        )
        assert reminder.status == "pending"
        assert not reminder.is_overdue
        assert reminder.overdue_duration is None

    def test_pending_past_due_is_overdue(self, tenant, admin_user):
        """A pending reminder past its scheduled time should be overdue."""
        reminder = _create_reminder(
            tenant, admin_user,
            scheduled_at=timezone.now() - timedelta(hours=3),
        )
        assert reminder.status == "overdue"
        assert reminder.is_overdue
        assert reminder.overdue_duration is not None
        assert reminder.overdue_duration.total_seconds() > 0

    def test_completed_past_due_not_overdue(self, tenant, admin_user):
        """A completed reminder should not be overdue even if past due."""
        reminder = _create_reminder(
            tenant, admin_user,
            scheduled_at=timezone.now() - timedelta(hours=3),
            completed_at=timezone.now() - timedelta(hours=1),
        )
        assert reminder.status == "completed"
        assert not reminder.is_overdue

    def test_cancelled_past_due_not_overdue(self, tenant, admin_user):
        """A cancelled reminder should not be overdue even if past due."""
        reminder = _create_reminder(
            tenant, admin_user,
            scheduled_at=timezone.now() - timedelta(hours=3),
            cancelled_at=timezone.now() - timedelta(hours=2),
        )
        assert reminder.status == "cancelled"
        assert not reminder.is_overdue

    def test_mark_completed(self, tenant, admin_user):
        """mark_completed() should set completed_at and change status."""
        reminder = _create_reminder(
            tenant, admin_user,
            scheduled_at=timezone.now() - timedelta(hours=1),
        )
        assert reminder.status == "overdue"
        reminder.mark_completed()
        reminder.refresh_from_db()
        assert reminder.status == "completed"
        assert reminder.completed_at is not None

    def test_mark_cancelled(self, tenant, admin_user):
        """mark_cancelled() should set cancelled_at and change status."""
        reminder = _create_reminder(
            tenant, admin_user,
            scheduled_at=timezone.now() + timedelta(hours=1),
        )
        reminder.mark_cancelled()
        reminder.refresh_from_db()
        assert reminder.status == "cancelled"

    def test_reschedule_removes_overdue(self, tenant, admin_user):
        """Rescheduling to the future should remove overdue status."""
        reminder = _create_reminder(
            tenant, admin_user,
            scheduled_at=timezone.now() - timedelta(hours=5),
        )
        assert reminder.is_overdue
        reminder.reschedule(
            new_scheduled_at=timezone.now() + timedelta(days=1),
            note="Client requested later call",
        )
        reminder.refresh_from_db()
        assert reminder.status == "pending"
        assert not reminder.is_overdue
        assert "Client requested later call" in reminder.notes


# ---------------------------------------------------------------------------
# 2. Queryset helpers
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestReminderQuerySet:

    def test_overdue_queryset(self, tenant, admin_user):
        """overdue() queryset should only return overdue reminders."""
        now = timezone.now()

        # Overdue
        _create_reminder(tenant, admin_user, scheduled_at=now - timedelta(hours=2))
        # Pending (future)
        _create_reminder(tenant, admin_user, scheduled_at=now + timedelta(hours=2))
        # Completed (past due)
        _create_reminder(
            tenant, admin_user,
            scheduled_at=now - timedelta(hours=2),
            completed_at=now - timedelta(hours=1),
        )
        # Cancelled (past due)
        _create_reminder(
            tenant, admin_user,
            scheduled_at=now - timedelta(hours=2),
            cancelled_at=now - timedelta(hours=1),
        )

        from apps.crm.models import Reminder

        # Set tenant context for the scoped manager
        set_current_tenant(tenant)
        overdue = Reminder.objects.overdue()
        assert overdue.count() == 1

        pending = Reminder.objects.pending()
        # Overdue + future pending = 2
        assert pending.count() == 2
        clear_current_tenant()


# ---------------------------------------------------------------------------
# 3. Tenant isolation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestReminderTenantIsolation:

    def test_tenant_a_cannot_see_tenant_b_reminders(self, tenant, admin_user, admin_client):
        """Reminders from another tenant must not be visible."""
        from apps.accounts.models import Role

        tenant_b = TenantFactory(name="Tenant B", slug="tenant-b-reminder")
        user_b = UserFactory()
        role_b = Role.unscoped.get(tenant=tenant_b, slug="admin")
        MembershipFactory(user=user_b, tenant=tenant_b, role=role_b)

        # Create reminder in tenant B
        _create_reminder(
            tenant_b, user_b,
            scheduled_at=timezone.now() - timedelta(hours=1),
            subject="Tenant B reminder",
        )

        # Create reminder in tenant A
        _create_reminder(
            tenant, admin_user,
            scheduled_at=timezone.now() - timedelta(hours=1),
            subject="Tenant A reminder",
        )

        # Fetch as tenant A admin
        resp = admin_client.get("/api/v1/crm/reminders/")
        assert resp.status_code == 200
        results = resp.data.get("results", resp.data)
        subjects = [r["subject"] for r in results]
        assert "Tenant A reminder" in subjects
        assert "Tenant B reminder" not in subjects


# ---------------------------------------------------------------------------
# 4. API Endpoints
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestReminderAPI:

    def test_list_reminders(self, tenant, admin_user, admin_client):
        now = timezone.now()
        _create_reminder(tenant, admin_user, scheduled_at=now - timedelta(hours=1))
        _create_reminder(tenant, admin_user, scheduled_at=now + timedelta(hours=1))

        resp = admin_client.get("/api/v1/crm/reminders/")
        assert resp.status_code == 200
        results = resp.data.get("results", resp.data)
        assert len(results) == 2

    def test_list_overdue_only(self, tenant, admin_user, admin_client):
        now = timezone.now()
        _create_reminder(tenant, admin_user, scheduled_at=now - timedelta(hours=1))
        _create_reminder(tenant, admin_user, scheduled_at=now + timedelta(hours=1))

        resp = admin_client.get("/api/v1/crm/reminders/overdue/")
        assert resp.status_code == 200
        results = resp.data.get("results", resp.data)
        assert len(results) == 1
        assert results[0]["status"] == "overdue"

    def test_mine_filter(self, tenant, admin_user, agent_user, admin_client):
        now = timezone.now()
        _create_reminder(
            tenant, admin_user,
            scheduled_at=now - timedelta(hours=1),
            assigned_to=admin_user,
        )
        _create_reminder(
            tenant, admin_user,
            scheduled_at=now - timedelta(hours=1),
            assigned_to=agent_user,
        )

        resp = admin_client.get("/api/v1/crm/reminders/?mine=true")
        assert resp.status_code == 200
        results = resp.data.get("results", resp.data)
        assert len(results) == 1

    def test_complete_action(self, tenant, admin_user, admin_client):
        reminder = _create_reminder(
            tenant, admin_user,
            scheduled_at=timezone.now() - timedelta(hours=1),
        )
        resp = admin_client.post(f"/api/v1/crm/reminders/{reminder.id}/complete/")
        assert resp.status_code == 200
        assert resp.data["status"] == "completed"

    def test_complete_already_completed(self, tenant, admin_user, admin_client):
        reminder = _create_reminder(
            tenant, admin_user,
            scheduled_at=timezone.now() - timedelta(hours=1),
            completed_at=timezone.now(),
        )
        resp = admin_client.post(f"/api/v1/crm/reminders/{reminder.id}/complete/")
        assert resp.status_code == 400

    def test_cancel_action(self, tenant, admin_user, admin_client):
        reminder = _create_reminder(
            tenant, admin_user,
            scheduled_at=timezone.now() + timedelta(hours=1),
        )
        resp = admin_client.post(f"/api/v1/crm/reminders/{reminder.id}/cancel/")
        assert resp.status_code == 200
        assert resp.data["status"] == "cancelled"

    def test_reschedule_action(self, tenant, admin_user, admin_client):
        reminder = _create_reminder(
            tenant, admin_user,
            scheduled_at=timezone.now() - timedelta(hours=1),
        )
        new_time = (timezone.now() + timedelta(days=2)).isoformat()
        resp = admin_client.post(
            f"/api/v1/crm/reminders/{reminder.id}/reschedule/",
            {"scheduled_at": new_time, "note": "Moved to next week"},
            format="json",
        )
        assert resp.status_code == 200
        assert resp.data["status"] == "pending"

    def test_reschedule_removes_from_overdue(self, tenant, admin_user, admin_client):
        reminder = _create_reminder(
            tenant, admin_user,
            scheduled_at=timezone.now() - timedelta(hours=5),
        )

        # Verify it appears in overdue list
        resp = admin_client.get("/api/v1/crm/reminders/overdue/")
        results = resp.data.get("results", resp.data)
        ids = [r["id"] for r in results]
        assert str(reminder.id) in ids

        # Reschedule to future
        new_time = (timezone.now() + timedelta(days=1)).isoformat()
        admin_client.post(
            f"/api/v1/crm/reminders/{reminder.id}/reschedule/",
            {"scheduled_at": new_time},
            format="json",
        )

        # Verify it no longer appears in overdue list
        resp = admin_client.get("/api/v1/crm/reminders/overdue/")
        results = resp.data.get("results", resp.data)
        ids = [r["id"] for r in results]
        assert str(reminder.id) not in ids

    def test_create_reminder(self, tenant, admin_user, admin_client):
        scheduled = (timezone.now() + timedelta(days=1)).isoformat()
        resp = admin_client.post(
            "/api/v1/crm/reminders/",
            {
                "subject": "Follow up with client",
                "scheduled_at": scheduled,
                "priority": "high",
            },
            format="json",
        )
        assert resp.status_code == 201
        assert resp.data["subject"] == "Follow up with client"

    def test_bulk_complete(self, tenant, admin_user, admin_client):
        now = timezone.now()
        r1 = _create_reminder(tenant, admin_user, scheduled_at=now - timedelta(hours=1))
        r2 = _create_reminder(tenant, admin_user, scheduled_at=now - timedelta(hours=2))

        resp = admin_client.post(
            "/api/v1/crm/reminders/bulk-action/",
            {
                "action": "complete",
                "reminder_ids": [str(r1.id), str(r2.id)],
            },
            format="json",
        )
        assert resp.status_code == 200
        assert resp.data["updated"] == 2

    def test_bulk_reschedule(self, tenant, admin_user, admin_client):
        now = timezone.now()
        r1 = _create_reminder(tenant, admin_user, scheduled_at=now - timedelta(hours=1))
        new_time = (now + timedelta(days=3)).isoformat()

        resp = admin_client.post(
            "/api/v1/crm/reminders/bulk-action/",
            {
                "action": "reschedule",
                "reminder_ids": [str(r1.id)],
                "scheduled_at": new_time,
            },
            format="json",
        )
        assert resp.status_code == 200
        assert resp.data["updated"] == 1

    def test_stats_endpoint(self, tenant, admin_user, admin_client):
        now = timezone.now()
        _create_reminder(tenant, admin_user, scheduled_at=now - timedelta(hours=1))
        _create_reminder(tenant, admin_user, scheduled_at=now - timedelta(hours=2))
        _create_reminder(tenant, admin_user, scheduled_at=now + timedelta(hours=1))

        resp = admin_client.get("/api/v1/crm/reminders/stats/")
        assert resp.status_code == 200
        assert resp.data["total_overdue"] == 2
        assert resp.data["completed_today"] == 0

    def test_status_filter(self, tenant, admin_user, admin_client):
        now = timezone.now()
        _create_reminder(tenant, admin_user, scheduled_at=now - timedelta(hours=1))
        _create_reminder(
            tenant, admin_user,
            scheduled_at=now - timedelta(hours=1),
            completed_at=now,
        )

        resp = admin_client.get("/api/v1/crm/reminders/?status=completed")
        results = resp.data.get("results", resp.data)
        assert len(results) == 1
        assert results[0]["status"] == "completed"

    def test_priority_filter(self, tenant, admin_user, admin_client):
        _create_reminder(
            tenant, admin_user,
            scheduled_at=timezone.now() - timedelta(hours=1),
            priority="urgent",
        )
        _create_reminder(
            tenant, admin_user,
            scheduled_at=timezone.now() - timedelta(hours=1),
            priority="low",
        )

        resp = admin_client.get("/api/v1/crm/reminders/?priority=urgent")
        results = resp.data.get("results", resp.data)
        assert len(results) == 1
        assert results[0]["priority"] == "urgent"


# ---------------------------------------------------------------------------
# 5. RBAC / Permissions
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestReminderPermissions:

    def test_anon_cannot_access(self, anon_client):
        resp = anon_client.get("/api/v1/crm/reminders/")
        assert resp.status_code in (401, 403)

    def test_agent_sees_only_own_reminders(
        self, tenant, admin_user, agent_user, agent_client
    ):
        now = timezone.now()
        _create_reminder(
            tenant, admin_user,
            scheduled_at=now - timedelta(hours=1),
            assigned_to=agent_user,
        )
        _create_reminder(
            tenant, admin_user,
            scheduled_at=now - timedelta(hours=1),
            assigned_to=admin_user,
        )

        resp = agent_client.get("/api/v1/crm/reminders/")
        results = resp.data.get("results", resp.data)
        # Agent should see only their own (assigned_to=agent_user)
        # or ones they created (but admin_user created both)
        assert len(results) == 1

    def test_admin_sees_all_reminders(
        self, tenant, admin_user, agent_user, admin_client
    ):
        now = timezone.now()
        _create_reminder(
            tenant, admin_user,
            scheduled_at=now - timedelta(hours=1),
            assigned_to=agent_user,
        )
        _create_reminder(
            tenant, admin_user,
            scheduled_at=now - timedelta(hours=1),
            assigned_to=admin_user,
        )

        resp = admin_client.get("/api/v1/crm/reminders/")
        results = resp.data.get("results", resp.data)
        assert len(results) == 2


# ---------------------------------------------------------------------------
# 6. Dashboard integration
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDashboardReminders:

    def test_dashboard_includes_overdue_reminders(
        self, tenant, admin_user, admin_client, free_plan
    ):
        SubscriptionFactory(tenant=tenant, plan=free_plan)
        now = timezone.now()
        _create_reminder(
            tenant, admin_user,
            scheduled_at=now - timedelta(hours=1),
            subject="Call Mrs. Smith",
        )

        resp = admin_client.get("/api/v1/analytics/dashboard/")
        assert resp.status_code == 200
        reminders = resp.data.get("overdue_reminders", {})
        assert reminders["total"] == 1
        assert len(reminders["items"]) == 1
        assert reminders["items"][0]["subject"] == "Call Mrs. Smith"

    def test_dashboard_count_matches_api(
        self, tenant, admin_user, admin_client, free_plan
    ):
        SubscriptionFactory(tenant=tenant, plan=free_plan)
        now = timezone.now()
        _create_reminder(tenant, admin_user, scheduled_at=now - timedelta(hours=1))
        _create_reminder(tenant, admin_user, scheduled_at=now - timedelta(hours=2))
        _create_reminder(tenant, admin_user, scheduled_at=now + timedelta(hours=1))  # not overdue

        # Dashboard count
        resp = admin_client.get("/api/v1/analytics/dashboard/")
        dashboard_count = resp.data["overdue_reminders"]["total"]

        # API overdue endpoint count
        resp2 = admin_client.get("/api/v1/crm/reminders/overdue/")
        api_results = resp2.data.get("results", resp2.data)
        api_count = len(api_results)

        assert dashboard_count == api_count == 2


# ---------------------------------------------------------------------------
# 7. Celery task
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestReminderCeleryTask:

    def test_check_overdue_reminders_sends_notification(
        self, tenant, admin_user, celery_eager, settings
    ):
        """The task should create a REMINDER_OVERDUE notification."""
        from apps.crm.tasks import check_overdue_reminders
        from apps.notifications.models import Notification

        _create_reminder(
            tenant, admin_user,
            scheduled_at=timezone.now() - timedelta(hours=2),
            assigned_to=admin_user,
        )

        check_overdue_reminders()

        notifs = Notification.unscoped.filter(
            tenant=tenant,
            recipient=admin_user,
            type="reminder_overdue",
        )
        assert notifs.exists()

    def test_check_overdue_reminders_dedup(
        self, tenant, admin_user, celery_eager, settings
    ):
        """Running the task twice should not create duplicate notifications."""
        from apps.crm.tasks import check_overdue_reminders
        from apps.notifications.models import Notification

        _create_reminder(
            tenant, admin_user,
            scheduled_at=timezone.now() - timedelta(hours=2),
            assigned_to=admin_user,
        )

        check_overdue_reminders()
        check_overdue_reminders()

        count = Notification.unscoped.filter(
            tenant=tenant,
            recipient=admin_user,
            type="reminder_overdue",
        ).count()
        assert count == 1

    def test_completed_reminder_no_notification(
        self, tenant, admin_user, celery_eager, settings
    ):
        """Completed reminders should not trigger notifications."""
        from apps.crm.tasks import check_overdue_reminders
        from apps.notifications.models import Notification

        _create_reminder(
            tenant, admin_user,
            scheduled_at=timezone.now() - timedelta(hours=2),
            assigned_to=admin_user,
            completed_at=timezone.now() - timedelta(hours=1),
        )

        check_overdue_reminders()

        notifs = Notification.unscoped.filter(
            tenant=tenant,
            type="reminder_overdue",
        )
        assert not notifs.exists()


# ---------------------------------------------------------------------------
# 8. Timezone awareness
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestReminderTimezoneAwareness:

    def test_scheduled_at_is_timezone_aware(self, tenant, admin_user, admin_client):
        """Creating a reminder via API should enforce timezone-aware datetimes."""
        scheduled = (timezone.now() + timedelta(days=1)).isoformat()
        resp = admin_client.post(
            "/api/v1/crm/reminders/",
            {"subject": "TZ test", "scheduled_at": scheduled, "priority": "medium"},
            format="json",
        )
        assert resp.status_code == 201

        from apps.crm.models import Reminder
        set_current_tenant(tenant)
        reminder = Reminder.objects.get(pk=resp.data["id"])
        clear_current_tenant()
        assert reminder.scheduled_at.tzinfo is not None


# ---------------------------------------------------------------------------
# 9. Contact linkage
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestReminderContactLinkage:

    def test_reminder_with_contact(self, tenant, admin_user, admin_client):
        set_current_tenant(tenant)
        contact = ContactFactory(tenant=tenant)
        clear_current_tenant()

        reminder = _create_reminder(
            tenant, admin_user,
            scheduled_at=timezone.now() - timedelta(hours=1),
            contact=contact,
        )

        resp = admin_client.get(f"/api/v1/crm/reminders/{reminder.id}/")
        assert resp.status_code == 200
        contact_ids = [str(cid) for cid in resp.data["contacts"]]
        assert str(contact.id) in contact_ids
        display = resp.data["contacts_display"]
        assert len(display) == 1
        assert display[0]["name"] is not None

    def test_filter_by_contact(self, tenant, admin_user, admin_client):
        set_current_tenant(tenant)
        contact = ContactFactory(tenant=tenant)
        clear_current_tenant()

        _create_reminder(
            tenant, admin_user,
            scheduled_at=timezone.now() - timedelta(hours=1),
            contact=contact,
        )
        _create_reminder(
            tenant, admin_user,
            scheduled_at=timezone.now() - timedelta(hours=1),
        )

        resp = admin_client.get(f"/api/v1/crm/reminders/?contact={contact.id}")
        results = resp.data.get("results", resp.data)
        assert len(results) == 1


# ---------------------------------------------------------------------------
# 10. fire_due_reminders — the "popup when a reminder comes due" task
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFireDueReminders:
    """The one-shot due-reminder alert task that drives the can't-miss popup."""

    @staticmethod
    def _due_notifs(tenant, recipient=None):
        from apps.notifications.models import Notification

        qs = Notification.unscoped.filter(tenant=tenant, type="reminder_due")
        if recipient is not None:
            qs = qs.filter(recipient=recipient)
        return qs

    def test_fires_due_notification(self, tenant, admin_user):
        """A reminder past its scheduled time fires one REMINDER_DUE notif."""
        from apps.crm.tasks import fire_due_reminders

        _create_reminder(
            tenant, admin_user,
            scheduled_at=timezone.now() - timedelta(minutes=1),
            assigned_to=admin_user,
        )
        fire_due_reminders()
        assert self._due_notifs(tenant, admin_user).count() == 1

    def test_future_reminder_does_not_fire(self, tenant, admin_user):
        """A reminder still in the future is left alone."""
        from apps.crm.tasks import fire_due_reminders

        _create_reminder(
            tenant, admin_user,
            scheduled_at=timezone.now() + timedelta(hours=1),
            assigned_to=admin_user,
        )
        fire_due_reminders()
        assert not self._due_notifs(tenant).exists()

    def test_fires_only_once(self, tenant, admin_user):
        """Running the task repeatedly must not re-pop the same reminder."""
        from apps.crm.tasks import fire_due_reminders

        _create_reminder(
            tenant, admin_user,
            scheduled_at=timezone.now() - timedelta(minutes=5),
            assigned_to=admin_user,
        )
        fire_due_reminders()
        fire_due_reminders()
        assert self._due_notifs(tenant).count() == 1

    def test_completed_and_cancelled_excluded(self, tenant, admin_user):
        """Completed or cancelled reminders never pop."""
        from apps.crm.tasks import fire_due_reminders

        _create_reminder(
            tenant, admin_user,
            scheduled_at=timezone.now() - timedelta(minutes=5),
            assigned_to=admin_user,
            completed_at=timezone.now(),
        )
        _create_reminder(
            tenant, admin_user,
            scheduled_at=timezone.now() - timedelta(minutes=5),
            assigned_to=admin_user,
            cancelled_at=timezone.now(),
        )
        fire_due_reminders()
        assert not self._due_notifs(tenant).exists()

    def test_falls_back_to_creator_when_unassigned(self, tenant, admin_user):
        """An unassigned reminder alerts its creator instead of nobody."""
        from apps.crm.tasks import fire_due_reminders

        _create_reminder(
            tenant, admin_user,
            scheduled_at=timezone.now() - timedelta(minutes=2),
            assigned_to=None,
        )
        fire_due_reminders()
        # _create_reminder sets created_by=admin_user.
        assert self._due_notifs(tenant, admin_user).count() == 1

    def test_rearms_when_moved_forward(self, tenant, admin_user):
        """A reminder whose watermark trails its (new) scheduled time re-pops.

        Simulates: it already fired, then was moved to a later — but still
        past — time, so ``due_notified_at < scheduled_at`` and it re-arms.
        """
        from apps.crm.models import Reminder
        from apps.crm.tasks import fire_due_reminders

        reminder = _create_reminder(
            tenant, admin_user,
            scheduled_at=timezone.now() - timedelta(minutes=1),
            assigned_to=admin_user,
        )
        Reminder.unscoped.filter(pk=reminder.pk).update(
            due_notified_at=timezone.now() - timedelta(hours=2),
        )
        fire_due_reminders()
        assert self._due_notifs(tenant, admin_user).count() == 1
        reminder.refresh_from_db()
        # Watermark advanced to >= scheduled_at, so it will not pop again.
        assert reminder.due_notified_at >= reminder.scheduled_at

    def test_suppressed_when_already_notified(self, tenant, admin_user):
        """A watermark at/after the scheduled time keeps the reminder quiet."""
        from apps.crm.models import Reminder
        from apps.crm.tasks import fire_due_reminders

        reminder = _create_reminder(
            tenant, admin_user,
            scheduled_at=timezone.now() - timedelta(minutes=1),
            assigned_to=admin_user,
        )
        Reminder.unscoped.filter(pk=reminder.pk).update(
            due_notified_at=timezone.now(),
        )
        fire_due_reminders()
        assert not self._due_notifs(tenant).exists()

    def test_due_notification_is_internal_only(self, tenant, admin_user):
        """REMINDER_DUE is in-app only — it must never queue an email."""
        from apps.notifications.services import INTERNAL_ONLY_TYPES
        from apps.notifications.models import NotificationType

        assert NotificationType.REMINDER_DUE in INTERNAL_ONLY_TYPES

    def test_claim_first_prevents_refire_on_delivery_failure(
        self, tenant, admin_user, monkeypatch
    ):
        """If delivery fails, the watermark is still claimed so the alert can
        never re-fire every tick (a duplicate storm)."""
        import apps.notifications.services as notif_services
        from apps.crm.tasks import fire_due_reminders

        reminder = _create_reminder(
            tenant, admin_user,
            scheduled_at=timezone.now() - timedelta(minutes=1),
            assigned_to=admin_user,
        )

        calls = {"n": 0}

        def boom(*args, **kwargs):
            calls["n"] += 1
            raise RuntimeError("delivery down")

        # The task imports send_notification from this module at call time.
        monkeypatch.setattr(notif_services, "send_notification", boom)

        fire_due_reminders()  # delivery raises, but the watermark is claimed

        reminder.refresh_from_db()
        assert reminder.due_notified_at is not None  # claimed despite failure
        assert calls["n"] == 1

        # A later tick must NOT retry the already-claimed reminder.
        fire_due_reminders()
        assert calls["n"] == 1


# ---------------------------------------------------------------------------
# 8. bulk-action security (horizontal priv-esc / IDOR)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestReminderBulkActionSecurity:
    """Regression tests for the bulk-action horizontal privilege escalation.

    ``bulk_action`` previously built its target set from ``Reminder.objects``
    (tenant-scoped only), skipping the agent row restriction that
    ``get_object()`` applies for single-item actions — so an agent could
    complete/cancel/reschedule/reassign ANY reminder in the tenant, and reassign
    them to a user outside the tenant (global ``User.objects`` lookup).
    """

    def test_agent_cannot_bulk_complete_others_reminder(
        self, tenant, admin_user, agent_user, agent_client
    ):
        # Owned entirely by the admin — the agent is neither creator nor assignee.
        other = _create_reminder(
            tenant, admin_user,
            scheduled_at=timezone.now() - timedelta(hours=1),
            assigned_to=admin_user,
        )
        resp = agent_client.post(
            "/api/v1/crm/reminders/bulk-action/",
            {"action": "complete", "reminder_ids": [str(other.id)]},
            format="json",
        )
        assert resp.status_code == 404
        other.refresh_from_db()
        assert other.completed_at is None

    def test_agent_cannot_bulk_reassign_others_reminder(
        self, tenant, admin_user, agent_user, agent_client
    ):
        other = _create_reminder(
            tenant, admin_user,
            scheduled_at=timezone.now() - timedelta(hours=1),
            assigned_to=admin_user,
        )
        resp = agent_client.post(
            "/api/v1/crm/reminders/bulk-action/",
            {
                "action": "reassign",
                "reminder_ids": [str(other.id)],
                "assigned_to": str(agent_user.id),
            },
            format="json",
        )
        assert resp.status_code == 404
        other.refresh_from_db()
        assert other.assigned_to_id == admin_user.id

    def test_agent_can_bulk_complete_own_reminder(
        self, tenant, admin_user, agent_user, agent_client
    ):
        mine = _create_reminder(
            tenant, admin_user,
            scheduled_at=timezone.now() - timedelta(hours=1),
            assigned_to=agent_user,
        )
        resp = agent_client.post(
            "/api/v1/crm/reminders/bulk-action/",
            {"action": "complete", "reminder_ids": [str(mine.id)]},
            format="json",
        )
        assert resp.status_code == 200
        assert resp.data["updated"] == 1

    def test_bulk_reassign_to_non_member_rejected(
        self, tenant, tenant_b, admin_user, admin_client
    ):
        mine = _create_reminder(
            tenant, admin_user,
            scheduled_at=timezone.now() - timedelta(hours=1),
            assigned_to=admin_user,
        )
        foreign = MembershipFactory(tenant=tenant_b).user  # member of B only
        resp = admin_client.post(
            "/api/v1/crm/reminders/bulk-action/",
            {
                "action": "reassign",
                "reminder_ids": [str(mine.id)],
                "assigned_to": str(foreign.id),
            },
            format="json",
        )
        assert resp.status_code == 400
        mine.refresh_from_db()
        assert mine.assigned_to_id == admin_user.id

    def test_manager_can_bulk_reassign_to_member(
        self, tenant, admin_user, agent_user, manager_client
    ):
        r = _create_reminder(
            tenant, admin_user,
            scheduled_at=timezone.now() - timedelta(hours=1),
            assigned_to=admin_user,
        )
        resp = manager_client.post(
            "/api/v1/crm/reminders/bulk-action/",
            {
                "action": "reassign",
                "reminder_ids": [str(r.id)],
                "assigned_to": str(agent_user.id),
            },
            format="json",
        )
        assert resp.status_code == 200
        assert resp.data["updated"] == 1
        r.refresh_from_db()
        assert r.assigned_to_id == agent_user.id

    def test_agent_cannot_bulk_cancel_others_reminder(
        self, tenant, admin_user, agent_user, agent_client
    ):
        other = _create_reminder(
            tenant, admin_user,
            scheduled_at=timezone.now() - timedelta(hours=1),
            assigned_to=admin_user,
        )
        resp = agent_client.post(
            "/api/v1/crm/reminders/bulk-action/",
            {"action": "cancel", "reminder_ids": [str(other.id)]},
            format="json",
        )
        assert resp.status_code == 404
        other.refresh_from_db()
        assert other.cancelled_at is None

    def test_agent_cannot_bulk_reschedule_others_reminder(
        self, tenant, admin_user, agent_user, agent_client
    ):
        original = timezone.now() - timedelta(hours=1)
        other = _create_reminder(
            tenant, admin_user, scheduled_at=original, assigned_to=admin_user,
        )
        resp = agent_client.post(
            "/api/v1/crm/reminders/bulk-action/",
            {
                "action": "reschedule",
                "reminder_ids": [str(other.id)],
                "scheduled_at": (timezone.now() + timedelta(days=3)).isoformat(),
            },
            format="json",
        )
        assert resp.status_code == 404
        other.refresh_from_db()
        assert abs((other.scheduled_at - original).total_seconds()) < 1

    def test_bulk_reassign_to_inactive_member_rejected(
        self, tenant, admin_user, admin_client
    ):
        """The is_active=True clause: a deactivated membership is not a valid
        reassign target even though the user was once in the tenant."""
        from apps.accounts.models import TenantMembership

        r = _create_reminder(
            tenant, admin_user,
            scheduled_at=timezone.now() - timedelta(hours=1),
            assigned_to=admin_user,
        )
        deactivated = MembershipFactory(tenant=tenant, is_active=False).user
        resp = admin_client.post(
            "/api/v1/crm/reminders/bulk-action/",
            {
                "action": "reassign",
                "reminder_ids": [str(r.id)],
                "assigned_to": str(deactivated.id),
            },
            format="json",
        )
        assert resp.status_code == 400
        r.refresh_from_db()
        assert r.assigned_to_id == admin_user.id

    def test_agent_mixed_ownership_batch_all_or_nothing(
        self, tenant, admin_user, agent_user, agent_client
    ):
        """A batch mixing an owned + a not-owned reminder is rejected wholesale
        (found_count guard), so nothing is mutated."""
        mine = _create_reminder(
            tenant, admin_user,
            scheduled_at=timezone.now() - timedelta(hours=1),
            assigned_to=agent_user,
        )
        other = _create_reminder(
            tenant, admin_user,
            scheduled_at=timezone.now() - timedelta(hours=1),
            assigned_to=admin_user,
        )
        resp = agent_client.post(
            "/api/v1/crm/reminders/bulk-action/",
            {"action": "complete", "reminder_ids": [str(mine.id), str(other.id)]},
            format="json",
        )
        assert resp.status_code == 404
        mine.refresh_from_db()
        assert mine.completed_at is None  # own reminder untouched too
