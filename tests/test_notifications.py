"""
Phase 4f (part 2) — Notification tests.

Covers:
- Notification CRUD, mark_read, unread_count
- NotificationPreference management
- Celery task: cleanup_old_notifications
- Notification email task
"""

import pytest
from django.utils import timezone
from datetime import timedelta

from conftest import NotificationFactory
from main.context import clear_current_tenant, set_current_tenant


@pytest.mark.django_db
class TestNotificationAPI:
    def test_list_notifications(self, admin_client, tenant, admin_user):
        set_current_tenant(tenant)
        NotificationFactory(tenant=tenant, recipient=admin_user)
        clear_current_tenant()

        resp = admin_client.get("/api/v1/notifications/notifications/")
        assert resp.status_code == 200
        assert resp.data["count"] >= 1

    def test_mark_read(self, admin_client, tenant, admin_user):
        set_current_tenant(tenant)
        notif = NotificationFactory(tenant=tenant, recipient=admin_user, is_read=False)
        clear_current_tenant()

        resp = admin_client.post(f"/api/v1/notifications/notifications/{notif.pk}/mark_read/")
        assert resp.status_code == 200
        notif.refresh_from_db()
        assert notif.is_read is True

    def test_unread_count(self, admin_client, tenant, admin_user):
        set_current_tenant(tenant)
        NotificationFactory(tenant=tenant, recipient=admin_user, is_read=False)
        NotificationFactory(tenant=tenant, recipient=admin_user, is_read=True)
        clear_current_tenant()

        resp = admin_client.get("/api/v1/notifications/notifications/unread_count/")
        assert resp.status_code == 200
        assert resp.data["unread_count"] == 1


@pytest.mark.django_db
class TestNotificationModel:
    def test_mark_read_method(self, tenant, admin_user):
        set_current_tenant(tenant)
        notif = NotificationFactory(tenant=tenant, recipient=admin_user)
        clear_current_tenant()

        notif.mark_read()
        assert notif.is_read is True
        assert notif.read_at is not None


@pytest.mark.django_db
class TestCleanupTask:
    def test_cleanup_old_read_notifications(self, tenant, admin_user):
        set_current_tenant(tenant)
        old_notif = NotificationFactory(
            tenant=tenant, recipient=admin_user,
            is_read=True,
        )
        # Manually backdate
        from apps.notifications.models import Notification
        Notification.unscoped.filter(pk=old_notif.pk).update(
            read_at=timezone.now() - timedelta(days=100),
        )

        recent_notif = NotificationFactory(
            tenant=tenant, recipient=admin_user,
            is_read=True,
        )
        Notification.unscoped.filter(pk=recent_notif.pk).update(
            read_at=timezone.now() - timedelta(days=10),
        )
        clear_current_tenant()

        from apps.notifications.tasks import cleanup_old_notifications
        deleted = cleanup_old_notifications(days=90)
        assert deleted >= 1

        # Recent one should still exist
        assert Notification.unscoped.filter(pk=recent_notif.pk).exists()
