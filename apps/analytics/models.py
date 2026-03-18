"""
Models for the analytics app.

Provides ReportDefinition, DashboardWidget, and ExportJob for tenant-scoped
reporting, dashboard configuration, and asynchronous data exports.
"""

from django.conf import settings
from django.db import models

from main.models import TenantScopedModel


class ReportDefinition(TenantScopedModel):
    """
    Tenant-scoped report template.

    Stores the configuration for a reusable report including its type,
    filter criteria, and the user who created it. Reports can be executed
    on-demand or used as the basis for an ExportJob.
    """

    class ReportType(models.TextChoices):
        TICKET_VOLUME = "ticket_volume", "Ticket Volume"
        SLA_COMPLIANCE = "sla_compliance", "SLA Compliance"
        AGENT_PERFORMANCE = "agent_performance", "Agent Performance"
        CONTACT_GROWTH = "contact_growth", "Contact Growth"
        TICKET_BY_PRIORITY = "ticket_by_priority", "Ticket by Priority"
        TICKET_BY_STATUS = "ticket_by_status", "Ticket by Status"
        RESPONSE_TIME = "response_time", "Response Time"

    name = models.CharField(max_length=100)
    report_type = models.CharField(
        max_length=50,
        choices=ReportType.choices,
    )
    filters = models.JSONField(
        default=dict,
        blank=True,
        help_text="Stored filter criteria for this report (e.g. date range, priority).",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="report_definitions",
    )

    class Meta:
        verbose_name = "report definition"
        verbose_name_plural = "report definitions"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.get_report_type_display()})"


class DashboardWidget(TenantScopedModel):
    """
    Tenant-scoped dashboard widget configuration.

    Each widget defines a visual component on a user's dashboard, including
    the chart type, data source reference, filters, and grid position.
    When ``user`` is set, the widget is personal; otherwise it is shared
    across the tenant.
    """

    class WidgetType(models.TextChoices):
        COUNTER = "counter", "Counter"
        CHART_BAR = "chart_bar", "Bar Chart"
        CHART_LINE = "chart_line", "Line Chart"
        CHART_PIE = "chart_pie", "Pie Chart"
        TABLE = "table", "Table"

    title = models.CharField(max_length=100)
    widget_type = models.CharField(
        max_length=20,
        choices=WidgetType.choices,
    )
    data_source = models.CharField(
        max_length=100,
        help_text='Reference to the data provider (e.g. "tickets.open_count").',
    )
    filters = models.JSONField(
        default=dict,
        blank=True,
        help_text="Optional filter criteria applied to this widget's data.",
    )
    position = models.JSONField(
        default=dict,
        blank=True,
        help_text='Grid position and size (e.g. {"x": 0, "y": 0, "w": 6, "h": 4}).',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="dashboard_widgets",
        help_text="If set, this widget is personal to this user.",
    )

    class Meta:
        verbose_name = "dashboard widget"
        verbose_name_plural = "dashboard widgets"
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class ExportJob(TenantScopedModel):
    """
    Tenant-scoped asynchronous export job.

    Created when a user requests a data export. A Celery task processes the
    export in the background, generating the file and updating the status
    to ``completed`` or ``failed``.
    """

    class ExportType(models.TextChoices):
        CSV = "csv", "CSV"
        XLSX = "xlsx", "XLSX"
        PDF = "pdf", "PDF"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    report = models.ForeignKey(
        ReportDefinition,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="export_jobs",
        help_text="Optional report definition this export is based on.",
    )
    export_type = models.CharField(
        max_length=20,
        choices=ExportType.choices,
    )
    resource_type = models.CharField(
        max_length=50,
        help_text='The type of resource being exported (e.g. "tickets", "contacts").',
    )
    filters = models.JSONField(
        default=dict,
        blank=True,
        help_text="Filter criteria for the exported data.",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    file = models.FileField(
        upload_to="exports/",
        null=True,
        blank=True,
        help_text="Generated export file.",
    )
    error_message = models.TextField(
        blank=True,
        default="",
        help_text="Error details if the export failed.",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="export_jobs",
    )
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = "export job"
        verbose_name_plural = "export jobs"
        ordering = ["-created_at"]

    def __str__(self):
        return (
            f"Export {self.resource_type} ({self.get_export_type_display()}) "
            f"- {self.get_status_display()}"
        )


class CalendarEvent(TenantScopedModel):
    """
    Tenant-scoped calendar event.

    Allows users to create personal events on the calendar such as
    meetings, calls, tasks, and reminders.
    """

    class EventType(models.TextChoices):
        MEETING = "meeting", "Meeting"
        CALL = "call", "Call"
        TASK = "task", "Task"
        REMINDER = "reminder", "Reminder"
        OTHER = "other", "Other"

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    event_date = models.DateField()
    event_time = models.TimeField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    event_type = models.CharField(
        max_length=20,
        choices=EventType.choices,
        default=EventType.OTHER,
    )
    is_all_day = models.BooleanField(default=False)
    location = models.CharField(max_length=500, blank=True, default="")
    color = models.CharField(max_length=7, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="calendar_events",
    )

    class Meta:
        verbose_name = "calendar event"
        verbose_name_plural = "calendar events"
        ordering = ["event_date", "event_time"]

    def __str__(self):
        return f"{self.title} ({self.event_date})"
