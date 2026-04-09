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
    make_api_client,
)
from main.context import clear_current_tenant, set_current_tenant


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_reminder(tenant, user, **kwargs):
    """Create a reminder with tenant context set."""
    set_current_tenant(tenant)
    reminder = ReminderFactory(tenant=tenant, created_by=user, **kwargs)
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
        assert str(resp.data["contact"]) == str(contact.id)
        assert resp.data["contact_name"] is not None

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
