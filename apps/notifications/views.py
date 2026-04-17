"""
DRF ViewSets for the notifications app.

* ``NotificationViewSet``           -- list, mark_read, mark_all_read, unread_count.
* ``NotificationPreferenceViewSet`` -- list/update delivery preferences per type.
"""

import logging

from django.utils import timezone
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import IsTenantMember
from apps.notifications.models import (
    Notification,
    NotificationPreference,
    NotificationType,
)
from apps.notifications.serializers import (
    NotificationPreferenceSerializer,
    NotificationSerializer,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# NotificationViewSet
# ---------------------------------------------------------------------------


class NotificationViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """
    Notification endpoints for the authenticated user.

    **list** -- paginated notifications, unread first then by newest.

    Custom actions:
        - ``POST /mark_read/``     -- mark a single notification as read.
        - ``POST /mark_all_read/`` -- mark all unread notifications as read.
        - ``GET  /unread_count/``  -- return the count of unread notifications.
    """

    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated, IsTenantMember]

    def get_queryset(self):
        """
        Return notifications for the current user within the active tenant.

        Supports ``?is_read=true/false`` query param filtering.
        Orders unread notifications first, then by newest ``created_at``.
        """
        if getattr(self, "swagger_fake_view", False):
            return Notification.objects.none()
        qs = Notification.objects.filter(recipient=self.request.user)
        tenant = getattr(self.request, "tenant", None)
        if tenant is not None:
            qs = qs.filter(tenant=tenant)
        is_read_param = self.request.query_params.get("is_read")
        if is_read_param is not None:
            qs = qs.filter(is_read=is_read_param.lower() in ("true", "1"))
        return qs.order_by("is_read", "-created_at")

    # ----- Custom actions ------------------------------------------------

    @action(detail=True, methods=["post"], url_path="mark_read")
    def mark_read(self, request, pk=None):
        """Mark a single notification as read."""
        notification = self.get_object()
        if notification.recipient_id != request.user.id:
            return Response(
                {"detail": "Not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        notification.mark_read()
        serializer = self.get_serializer(notification)
        return Response(serializer.data)

    @action(detail=False, methods=["post"], url_path="mark_all_read")
    def mark_all_read(self, request):
        """Mark all unread notifications for the current user as read."""
        now = timezone.now()
        updated = (
            Notification.objects.filter(
                recipient=request.user,
                is_read=False,
            )
            .update(is_read=True, read_at=now)
        )
        return Response({"updated": updated})

    @action(detail=False, methods=["get"], url_path="unread_count")
    def unread_count(self, request):
        """Return the number of unread notifications for the current user."""
        count = Notification.objects.filter(
            recipient=request.user,
            is_read=False,
        ).count()
        return Response({"unread_count": count})

    @action(detail=False, methods=["post"], url_path="cleanup")
    def cleanup(self, request):
        """Trigger cleanup of read notifications older than 90 days (admin only)."""
        from apps.accounts.permissions import IsTenantAdmin

        perm = IsTenantAdmin()
        if not perm.has_permission(request, self):
            return Response(
                {"detail": "Admin access required."},
                status=status.HTTP_403_FORBIDDEN,
            )

        from apps.notifications.tasks import cleanup_old_notifications

        cleanup_old_notifications.delay(days=90)
        return Response({"detail": "Cleanup task queued. Old read notifications will be removed shortly."})


# ---------------------------------------------------------------------------
# NotificationPreferenceViewSet
# ---------------------------------------------------------------------------


class NotificationPreferenceViewSet(
    mixins.ListModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """
    Notification delivery preferences for the authenticated user.

    **list**   -- returns preferences for all notification types. Types
                  without an explicit preference record are returned with
                  the defaults (in_app=True, email=True).
    **update** -- create or update a preference for a specific type.
    """

    serializer_class = NotificationPreferenceSerializer
    permission_classes = [permissions.IsAuthenticated, IsTenantMember]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return NotificationPreference.objects.none()
        return NotificationPreference.objects.filter(user=self.request.user)

    def list(self, request, *args, **kwargs):
        """
        Return preferences for every notification type.

        If the user has not explicitly set a preference for a type, a
        virtual entry with defaults is included so the frontend always
        receives the full list.
        """
        existing = {
            pref.notification_type: pref
            for pref in self.get_queryset()
        }

        results = []
        for choice_value, choice_label in NotificationType.choices:
            if choice_value in existing:
                results.append(
                    self.get_serializer(existing[choice_value]).data
                )
            else:
                results.append(
                    {
                        "id": None,
                        "notification_type": choice_value,
                        "notification_type_display": choice_label,
                        "in_app": True,
                        "email": True,
                        "created_at": None,
                        "updated_at": None,
                    }
                )

        return Response(results)

    def update(self, request, *args, **kwargs):
        """
        Create or update a notification preference.

        Uses ``update_or_create`` keyed on (user, tenant, notification_type)
        so that clients can PUT without knowing whether a record already
        exists.
        """
        partial = kwargs.pop("partial", False)
        notification_type = request.data.get("notification_type")

        if notification_type not in dict(NotificationType.choices):
            return Response(
                {"notification_type": [f"Invalid type: {notification_type}"]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        tenant = getattr(request, "tenant", None)
        if tenant is None:
            return Response(
                {"detail": "No tenant context available."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        preference, created = NotificationPreference.unscoped.update_or_create(
            user=request.user,
            tenant=tenant,
            notification_type=notification_type,
            defaults={
                "in_app": request.data.get("in_app", True),
                "email": request.data.get("email", True),
            },
        )

        serializer = self.get_serializer(preference)
        return Response(
            serializer.data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def partial_update(self, request, *args, **kwargs):
        """Support PATCH via the same update logic."""
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)
