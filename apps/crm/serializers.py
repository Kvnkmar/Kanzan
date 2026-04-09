from django.utils import timezone
from rest_framework import serializers

from apps.crm.models import Activity, Reminder


class ActivitySerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField()
    assigned_to_name = serializers.SerializerMethodField()

    class Meta:
        model = Activity
        fields = [
            "id",
            "activity_type",
            "subject",
            "notes",
            "due_at",
            "completed_at",
            "outcome",
            "ticket",
            "contact",
            "created_by",
            "created_by_name",
            "assigned_to",
            "assigned_to_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_by", "created_at", "updated_at"]

    def get_created_by_name(self, obj):
        return obj.created_by.get_full_name() if obj.created_by else None

    def get_assigned_to_name(self, obj):
        return obj.assigned_to.get_full_name() if obj.assigned_to else None


class ReminderSerializer(serializers.ModelSerializer):
    """Full reminder serializer with computed fields."""

    status = serializers.SerializerMethodField()
    overdue_duration_seconds = serializers.SerializerMethodField()
    overdue_display = serializers.SerializerMethodField()
    contact_name = serializers.SerializerMethodField()
    contact_email = serializers.SerializerMethodField()
    assigned_to_name = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()
    ticket_number = serializers.SerializerMethodField()
    ticket_subject = serializers.SerializerMethodField()

    class Meta:
        model = Reminder
        fields = [
            "id",
            "subject",
            "notes",
            "scheduled_at",
            "completed_at",
            "cancelled_at",
            "priority",
            "status",
            "overdue_duration_seconds",
            "overdue_display",
            "contact",
            "contact_name",
            "contact_email",
            "ticket",
            "ticket_number",
            "ticket_subject",
            "assigned_to",
            "assigned_to_name",
            "created_by",
            "created_by_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "created_by",
            "completed_at",
            "cancelled_at",
            "created_at",
            "updated_at",
        ]

    def get_status(self, obj):
        return obj.status

    def get_overdue_duration_seconds(self, obj):
        dur = obj.overdue_duration
        if dur is not None:
            return int(dur.total_seconds())
        return None

    def get_overdue_display(self, obj):
        dur = obj.overdue_duration
        if dur is None:
            return None
        total_seconds = int(dur.total_seconds())
        if total_seconds < 3600:
            minutes = total_seconds // 60
            return f"{minutes}m overdue"
        elif total_seconds < 86400:
            hours = total_seconds // 3600
            return f"{hours}h overdue"
        else:
            days = total_seconds // 86400
            return f"{days}d overdue"

    def get_contact_name(self, obj):
        if obj.contact:
            return obj.contact.full_name
        return None

    def get_contact_email(self, obj):
        if obj.contact:
            return obj.contact.email
        return None

    def get_assigned_to_name(self, obj):
        return obj.assigned_to.get_full_name() if obj.assigned_to else None

    def get_created_by_name(self, obj):
        return obj.created_by.get_full_name() if obj.created_by else None

    def get_ticket_number(self, obj):
        return obj.ticket.number if obj.ticket else None

    def get_ticket_subject(self, obj):
        return obj.ticket.subject if obj.ticket else None


class ReminderCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating/updating reminders."""

    class Meta:
        model = Reminder
        fields = [
            "id",
            "subject",
            "notes",
            "scheduled_at",
            "priority",
            "contact",
            "ticket",
            "assigned_to",
        ]
        read_only_fields = ["id"]

    def validate_scheduled_at(self, value):
        if value and timezone.is_naive(value):
            raise serializers.ValidationError(
                "scheduled_at must be a timezone-aware datetime."
            )
        return value


class ReminderRescheduleSerializer(serializers.Serializer):
    """Serializer for rescheduling a reminder."""

    scheduled_at = serializers.DateTimeField()
    note = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_scheduled_at(self, value):
        if value and timezone.is_naive(value):
            raise serializers.ValidationError(
                "scheduled_at must be a timezone-aware datetime."
            )
        return value


class ReminderBulkActionSerializer(serializers.Serializer):
    """Serializer for bulk reminder actions."""

    action = serializers.ChoiceField(
        choices=["complete", "reschedule", "reassign", "cancel"]
    )
    reminder_ids = serializers.ListField(
        child=serializers.UUIDField(), min_length=1
    )
    scheduled_at = serializers.DateTimeField(required=False)
    assigned_to = serializers.UUIDField(required=False)
    note = serializers.CharField(required=False, allow_blank=True, default="")

    def validate(self, data):
        action = data["action"]
        if action == "reschedule" and not data.get("scheduled_at"):
            raise serializers.ValidationError(
                {"scheduled_at": "Required for reschedule action."}
            )
        if action == "reassign" and not data.get("assigned_to"):
            raise serializers.ValidationError(
                {"assigned_to": "Required for reassign action."}
            )
        return data
