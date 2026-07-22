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


@pytest.mark.django_db(transaction=True)
class TestSendNotificationMembershipGuard:
    """send_notification must not DELIVER a tenant's notification to a user who
    is not an active member of that tenant. The per-user WebSocket group is not
    tenant-scoped, so a stale/cross-tenant recipient would otherwise keep
    receiving live previews. Row is persisted; only delivery is suppressed."""

    def _patch_delivery(self, monkeypatch):
        from apps.notifications import services
        pushes, emails = [], []
        monkeypatch.setattr(services, "_push_to_websocket", lambda n: pushes.append(n))
        monkeypatch.setattr(services, "_queue_email", lambda n: emails.append(n))
        return pushes, emails

    def test_delivery_suppressed_for_non_member(self, monkeypatch):
        from apps.notifications import services
        from apps.notifications.models import Notification, NotificationType
        from conftest import MembershipFactory, TenantFactory

        tenant = TenantFactory(slug="notif-guard-a")
        other = TenantFactory(slug="notif-guard-b")
        foreign = MembershipFactory(tenant=other).user  # not a member of `tenant`

        pushes, emails = self._patch_delivery(monkeypatch)
        notif = services.send_notification(
            tenant, foreign, NotificationType.MESSAGE, "hi",
        )
        assert pushes == []          # no live preview to a non-member
        assert emails == []
        # The row is still persisted (tenant-scoped, unreadable by a non-member).
        assert Notification.unscoped.filter(pk=notif.pk).exists()

    def test_delivery_reaches_member(self, monkeypatch):
        from apps.notifications import services
        from apps.notifications.models import NotificationType
        from conftest import MembershipFactory, TenantFactory

        tenant = TenantFactory(slug="notif-guard-c")
        member = MembershipFactory(tenant=tenant).user

        pushes, _ = self._patch_delivery(monkeypatch)
        services.send_notification(
            tenant, member, NotificationType.MESSAGE, "hi",
        )
        assert len(pushes) == 1      # active member still gets the live push

    def test_delivery_suppressed_for_deactivated_member(self, monkeypatch):
        """The exact 'lost membership' case the fix targets: a user who WAS an
        active member but whose membership was set is_active=False must stop
        receiving live pushes (the is_active clause of the guard)."""
        from apps.notifications import services
        from apps.notifications.models import NotificationType
        from conftest import MembershipFactory, TenantFactory

        tenant = TenantFactory(slug="notif-guard-deact")
        member = MembershipFactory(tenant=tenant, is_active=False).user  # deactivated

        pushes, emails = self._patch_delivery(monkeypatch)
        services.send_notification(
            tenant, member, NotificationType.MESSAGE, "hi",
        )
        assert pushes == []
        assert emails == []
