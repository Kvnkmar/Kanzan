"""
DRF serializers for the notifications app.

Provides:
- ``NotificationSerializer`` -- read-only representation of a notification.
- ``NotificationPreferenceSerializer`` -- read/write for delivery preferences.
"""

from rest_framework import serializers

from apps.notifications.models import Notification, NotificationPreference


class NotificationSerializer(serializers.ModelSerializer):
    """
    Read-only serializer for ``Notification`` instances.

    Includes the human-readable notification type label via ``type_display``.
    """

    type_display = serializers.CharField(
        source="get_type_display",
        read_only=True,
    )

    class Meta:
        model = Notification
        fields = [
            "id",
            "recipient",
            "type",
            "type_display",
            "title",
            "body",
            "data",
            "is_read",
            "read_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    """
    Read/write serializer for ``NotificationPreference``.

    The ``user`` and ``tenant`` are set automatically from the request
    context, so clients only need to supply ``notification_type``,
    ``in_app``, and ``email``.
    """

    notification_type_display = serializers.CharField(
        source="get_notification_type_display",
        read_only=True,
    )

    class Meta:
        model = NotificationPreference
        fields = [
            "id",
            "notification_type",
            "notification_type_display",
            "in_app",
            "email",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "notification_type_display",
            "created_at",
            "updated_at",
        ]
