"""
DRF serializers for the analytics app.

Provides serializers for ReportDefinition, DashboardWidget, and ExportJob
models, including a dedicated create serializer for ExportJob that triggers
asynchronous processing.
"""

from rest_framework import serializers

from apps.analytics.models import CalendarEvent, DashboardWidget, ExportJob, ReportDefinition


# ---------------------------------------------------------------------------
# ReportDefinition
# ---------------------------------------------------------------------------


class ReportDefinitionSerializer(serializers.ModelSerializer):
    """Full CRUD serializer for report definitions."""

    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ReportDefinition
        fields = [
            "id",
            "name",
            "report_type",
            "filters",
            "created_by",
            "created_by_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_by", "created_by_name", "created_at", "updated_at"]

    def get_created_by_name(self, obj):
        if obj.created_by:
            full = f"{obj.created_by.first_name} {obj.created_by.last_name}".strip()
            return full or str(obj.created_by)
        return None

    def create(self, validated_data):
        request = self.context.get("request")
        if request and hasattr(request, "user"):
            validated_data["created_by"] = request.user
        return super().create(validated_data)


# ---------------------------------------------------------------------------
# DashboardWidget
# ---------------------------------------------------------------------------


class DashboardWidgetSerializer(serializers.ModelSerializer):
    """Full CRUD serializer for dashboard widgets."""

    class Meta:
        model = DashboardWidget
        fields = [
            "id",
            "title",
            "widget_type",
            "data_source",
            "filters",
            "position",
            "user",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


# ---------------------------------------------------------------------------
# ExportJob
# ---------------------------------------------------------------------------


class ExportJobSerializer(serializers.ModelSerializer):
    """Read-only serializer for listing export jobs and checking status."""

    requested_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ExportJob
        fields = [
            "id",
            "report",
            "export_type",
            "resource_type",
            "filters",
            "status",
            "file",
            "error_message",
            "requested_by",
            "requested_by_name",
            "completed_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_requested_by_name(self, obj):
        if obj.requested_by:
            full = f"{obj.requested_by.first_name} {obj.requested_by.last_name}".strip()
            return full or str(obj.requested_by)
        return None


class ExportJobCreateSerializer(serializers.ModelSerializer):
    """
    Create serializer for export jobs.

    On creation, sets the requesting user and triggers the async Celery task.
    """

    class Meta:
        model = ExportJob
        fields = [
            "id",
            "report",
            "export_type",
            "resource_type",
            "filters",
            "status",
            "created_at",
        ]
        read_only_fields = ["id", "status", "created_at"]

    def validate_resource_type(self, value):
        allowed = ("tickets", "contacts")
        if value not in allowed:
            raise serializers.ValidationError(
                f"resource_type must be one of: {', '.join(allowed)}"
            )
        return value

    def create(self, validated_data):
        request = self.context.get("request")
        validated_data["requested_by"] = request.user
        instance = super().create(validated_data)

        # Trigger async export processing.
        from apps.analytics.tasks import process_export_job

        process_export_job.delay(str(instance.id))

        return instance


# ---------------------------------------------------------------------------
# CalendarEvent
# ---------------------------------------------------------------------------


class CalendarEventSerializer(serializers.ModelSerializer):
    """Full CRUD serializer for calendar events."""

    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = CalendarEvent
        fields = [
            "id",
            "title",
            "description",
            "event_date",
            "event_time",
            "end_date",
            "end_time",
            "event_type",
            "is_all_day",
            "location",
            "color",
            "created_by",
            "created_by_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_by", "created_by_name", "created_at", "updated_at"]

    def get_created_by_name(self, obj):
        if obj.created_by:
            full = f"{obj.created_by.first_name} {obj.created_by.last_name}".strip()
            return full or str(obj.created_by)
        return None

    def create(self, validated_data):
        request = self.context.get("request")
        if request and hasattr(request, "user"):
            validated_data["created_by"] = request.user
        return super().create(validated_data)
