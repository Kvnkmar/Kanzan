"""
DRF ViewSet for file attachments.

Supports upload (create), list, retrieve, and delete operations,
with filtering by content_type + object_id.
"""

import logging

from django.contrib.contenttypes.models import ContentType
from django_filters import rest_framework as django_filters
from rest_framework import mixins, parsers, permissions, status, viewsets
from rest_framework.response import Response

from apps.accounts.permissions import IsTenantMember
from apps.attachments.models import Attachment
from apps.attachments.serializers import AttachmentSerializer, AttachmentUploadSerializer
from apps.comments.models import ActivityLog
from apps.comments.services import log_activity
from apps.tickets.models import TicketActivity

logger = logging.getLogger(__name__)

TICKET_CT_KEY = ("tickets", "ticket")


def _log_attachment_to_ticket(ticket, actor, attachment, event, action, request=None):
    """Write dual log entries (ActivityLog + TicketActivity) for attachment events."""
    msg = (
        f"{'Added' if event == TicketActivity.Event.ATTACHMENT_ADDED else 'Removed'}"
        f" attachment: {attachment.original_name}"
    )

    log_activity(
        tenant=ticket.tenant,
        actor=actor,
        content_object=ticket,
        action=action,
        description=msg,
        request=request,
    )

    TicketActivity.objects.create(
        tenant=ticket.tenant,
        ticket=ticket,
        actor=actor,
        event=event,
        message=msg,
        metadata={
            "attachment_id": str(attachment.pk),
            "file_name": attachment.original_name,
            "mime_type": attachment.mime_type,
            "size_bytes": attachment.size_bytes,
        },
    )


class AttachmentFilter(django_filters.FilterSet):
    """
    Filter attachments by the target content object.

    Usage:
        ?content_type=tickets.ticket&object_id=<uuid>
    """

    content_type = django_filters.CharFilter(method="filter_content_type")

    class Meta:
        model = Attachment
        fields = ["content_type", "object_id", "mime_type", "uploaded_by"]

    def filter_content_type(self, queryset, name, value):
        """Resolve 'app_label.model' to a ContentType filter."""
        try:
            app_label, model = value.strip().lower().split(".")
            ct = ContentType.objects.get(app_label=app_label, model=model)
            return queryset.filter(content_type=ct)
        except (ValueError, ContentType.DoesNotExist):
            return queryset.none()


class AttachmentViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """
    ViewSet for file attachments: upload, list, retrieve, and delete.

    Upload uses multipart/form-data. Files are validated for size and MIME
    type (via python-magic) before being stored in tenant-scoped paths.

    No update action is provided -- attachments are immutable once uploaded.
    To replace a file, delete the old attachment and upload a new one.
    """

    permission_classes = [permissions.IsAuthenticated, IsTenantMember]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]
    filterset_class = AttachmentFilter
    ordering_fields = ["created_at", "size_bytes", "original_name"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        if self.action == "create":
            return AttachmentUploadSerializer
        return AttachmentSerializer

    def get_queryset(self):
        return Attachment.objects.select_related(
            "uploaded_by", "content_type"
        )

    def create(self, request, *args, **kwargs):
        """
        Upload a new attachment.

        Accepts multipart/form-data with `file`, `content_type`, and `object_id`.
        Validates the file, detects MIME type, and stores with a tenant-scoped path.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Enforce storage limits before saving the file
        from apps.billing.services import PlanLimitChecker

        uploaded_file = request.FILES.get("file")
        if uploaded_file:
            size_mb = uploaded_file.size / (1024 * 1024)
            PlanLimitChecker(request.tenant).check_storage(size_mb)

        attachment = serializer.save()

        # Log to ticket timeline if this attachment targets a ticket
        ct = attachment.content_type
        if (ct.app_label, ct.model) == TICKET_CT_KEY:
            from apps.tickets.models import Ticket

            ticket = Ticket.objects.filter(pk=attachment.object_id).first()
            if ticket:
                _log_attachment_to_ticket(
                    ticket, request.user, attachment,
                    TicketActivity.Event.ATTACHMENT_ADDED,
                    ActivityLog.Action.ATTACHMENT_ADDED,
                    request=request,
                )

        # Return the read serializer representation.
        output_serializer = AttachmentSerializer(
            attachment, context={"request": request}
        )
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)

    def perform_destroy(self, instance):
        """Delete attachment record and remove the file from storage."""
        file_path = instance.file.name if instance.file else None

        # Log to ticket timeline before deleting
        ct = instance.content_type
        if (ct.app_label, ct.model) == TICKET_CT_KEY:
            from apps.tickets.models import Ticket

            ticket = Ticket.objects.filter(pk=instance.object_id).first()
            if ticket:
                _log_attachment_to_ticket(
                    ticket, self.request.user, instance,
                    TicketActivity.Event.ATTACHMENT_REMOVED,
                    ActivityLog.Action.ATTACHMENT_REMOVED,
                    request=self.request,
                )

        instance.file.delete(save=False)
        instance.delete()

        logger.info(
            "Attachment deleted: tenant=%s user=%s file=%s",
            instance.tenant_id,
            self.request.user.pk,
            file_path,
        )
