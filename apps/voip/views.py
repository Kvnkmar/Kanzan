"""
DRF ViewSets and views for the VoIP app.

Provides endpoints for VoIP settings, extensions, call logs, call
initiation/control, SIP credentials, and call recordings.
"""

import logging
import secrets

from django.http import FileResponse
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import HasTenantPermission, IsTenantMember
from apps.voip.models import (
    CallLog,
    CallQueue,
    CallRecording,
    Extension,
    VoIPSettings,
)
from apps.voip.serializers import (
    CallActionSerializer,
    CallLogDetailSerializer,
    CallLogListSerializer,
    CallLogUpdateSerializer,
    CallQueueSerializer,
    ExtensionCreateSerializer,
    ExtensionSerializer,
    InitiateCallSerializer,
    SIPCredentialsSerializer,
    VoIPSettingsSerializer,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# VoIP Settings (admin-only, singleton per tenant)
# ---------------------------------------------------------------------------


class VoIPSettingsViewSet(viewsets.ModelViewSet):
    """
    Per-tenant VoIP configuration.

    Only admins/managers can view or modify VoIP settings.
    Behaves as a singleton — list returns a single object.
    """

    serializer_class = VoIPSettingsSerializer
    permission_classes = [IsAuthenticated, HasTenantPermission]
    permission_resource = "voip_settings"
    http_method_names = ["get", "patch", "put"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return VoIPSettings.objects.none()
        return VoIPSettings.objects.filter(tenant=self.request.tenant)

    def list(self, request, *args, **kwargs):
        settings_obj, _ = VoIPSettings.objects.get_or_create(
            tenant=request.tenant,
        )
        serializer = self.get_serializer(settings_obj)
        return Response(serializer.data)

    def retrieve(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)


# ---------------------------------------------------------------------------
# Extensions
# ---------------------------------------------------------------------------


class ExtensionViewSet(viewsets.ModelViewSet):
    """
    CRUD for SIP extensions.

    Admins can manage all extensions. Agents can view their own.
    """

    permission_classes = [IsAuthenticated, HasTenantPermission]
    permission_resource = "voip_extension"
    search_fields = ["user__email", "user__first_name", "extension_number"]
    ordering_fields = ["extension_number", "created_at"]
    ordering = ["extension_number"]

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return ExtensionCreateSerializer
        return ExtensionSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Extension.objects.none()
        return Extension.objects.select_related("user").all()

    def perform_create(self, serializer):
        if not serializer.validated_data.get("sip_password"):
            serializer.validated_data["sip_password"] = secrets.token_urlsafe(32)
        serializer.save()


# ---------------------------------------------------------------------------
# SIP Credentials (per-user, session-authenticated)
# ---------------------------------------------------------------------------


class SIPCredentialsView(APIView):
    """
    Return SIP registration credentials for the current user.

    Used by the browser softphone to register with Asterisk.
    GET /api/v1/voip/sip-credentials/
    """

    permission_classes = [IsAuthenticated, IsTenantMember]

    def get(self, request):
        try:
            extension = Extension.objects.select_related("tenant").get(
                user=request.user,
                tenant=request.tenant,
                is_active=True,
            )
        except Extension.DoesNotExist:
            return Response(
                {"detail": "No active VoIP extension assigned to your account."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            voip_settings = VoIPSettings.objects.get(
                tenant=request.tenant,
                is_active=True,
            )
        except VoIPSettings.DoesNotExist:
            return Response(
                {"detail": "VoIP is not configured for this tenant."},
                status=status.HTTP_404_NOT_FOUND,
            )

        wss_url = (
            f"wss://{voip_settings.asterisk_host}:{voip_settings.asterisk_wss_port}/ws"
        )
        sip_uri = (
            f"sip:{extension.sip_username}@{voip_settings.asterisk_host}"
        )

        stun_servers = [voip_settings.stun_server] if voip_settings.stun_server else []
        turn_servers = []
        if voip_settings.turn_server:
            turn_servers.append({
                "urls": voip_settings.turn_server,
                "username": voip_settings.turn_username,
                "credential": voip_settings.turn_password,
            })

        data = {
            "sip_uri": sip_uri,
            "sip_password": extension.sip_password,
            "wss_url": wss_url,
            "stun_servers": stun_servers,
            "turn_servers": turn_servers,
            "extension_number": extension.extension_number,
            "caller_id_name": extension.caller_id_name or request.user.get_full_name(),
        }

        serializer = SIPCredentialsSerializer(data)
        return Response(serializer.data)


# ---------------------------------------------------------------------------
# Call Logs
# ---------------------------------------------------------------------------


class CallLogViewSet(viewsets.ModelViewSet):
    """
    Call history with filtering and search.

    Supports linking calls to contacts and tickets, and updating notes.
    """

    permission_classes = [IsAuthenticated, IsTenantMember]
    search_fields = ["caller_number", "callee_number", "notes"]
    filterset_fields = ["direction", "status", "contact", "ticket"]
    ordering_fields = ["started_at", "duration_seconds"]
    ordering = ["-started_at"]
    http_method_names = ["get", "patch"]

    def get_serializer_class(self):
        if self.action == "list":
            return CallLogListSerializer
        if self.action in ("partial_update", "update"):
            return CallLogUpdateSerializer
        return CallLogDetailSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return CallLog.objects.none()
        return (
            CallLog.objects.select_related(
                "caller_extension",
                "callee_extension",
                "contact",
                "ticket",
            )
            .all()
        )

    @action(detail=False, methods=["get"], url_path="active")
    def active_calls(self, request):
        """Return currently active calls for the tenant."""
        active = self.get_queryset().filter(
            status__in=[
                CallLog.Status.RINGING,
                CallLog.Status.ANSWERED,
                CallLog.Status.ON_HOLD,
            ]
        )
        serializer = CallLogListSerializer(active, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=["get"], url_path="stats")
    def call_stats(self, request):
        """Return call statistics for the current period."""
        from django.db.models import Avg, Count, Q, Sum

        qs = self.get_queryset()
        today = timezone.now().date()

        stats = qs.filter(started_at__date=today).aggregate(
            total_calls=Count("id"),
            answered_calls=Count("id", filter=Q(status=CallLog.Status.COMPLETED)),
            missed_calls=Count("id", filter=Q(status=CallLog.Status.MISSED)),
            total_duration=Sum("duration_seconds"),
            avg_duration=Avg("duration_seconds"),
            inbound_count=Count(
                "id", filter=Q(direction=CallLog.Direction.INBOUND)
            ),
            outbound_count=Count(
                "id", filter=Q(direction=CallLog.Direction.OUTBOUND)
            ),
        )

        return Response(stats)


# ---------------------------------------------------------------------------
# Call Initiation & Control
# ---------------------------------------------------------------------------


class InitiateCallView(APIView):
    """
    Initiate an outbound call via Asterisk ARI.

    POST /api/v1/voip/calls/initiate/
    {"callee_number": "+1234567890", "contact_id": "...", "ticket_id": "..."}
    """

    permission_classes = [IsAuthenticated, IsTenantMember]

    def post(self, request):
        serializer = InitiateCallSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Verify caller has an active extension
        try:
            caller_ext = Extension.objects.get(
                user=request.user,
                tenant=request.tenant,
                is_active=True,
            )
        except Extension.DoesNotExist:
            return Response(
                {"detail": "No active VoIP extension assigned."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Check billing limits
        from apps.voip.services import check_call_limit

        can_call, reason = check_call_limit(request.tenant)
        if not can_call:
            return Response(
                {"detail": reason},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Resolve contact if provided
        contact = None
        if serializer.validated_data.get("contact_id"):
            from apps.contacts.models import Contact

            contact = Contact.objects.filter(
                id=serializer.validated_data["contact_id"],
                tenant=request.tenant,
            ).first()

        # Resolve ticket if provided
        ticket = None
        if serializer.validated_data.get("ticket_id"):
            from apps.tickets.models import Ticket

            ticket = Ticket.objects.filter(
                id=serializer.validated_data["ticket_id"],
                tenant=request.tenant,
            ).first()

        # Create call log
        call_log = CallLog(
            tenant=request.tenant,
            direction=CallLog.Direction.OUTBOUND,
            status=CallLog.Status.RINGING,
            caller_extension=caller_ext,
            caller_number=caller_ext.caller_id_number or caller_ext.extension_number,
            callee_number=serializer.validated_data["callee_number"],
            contact=contact,
            ticket=ticket,
            started_at=timezone.now(),
        )
        call_log.save()

        # Originate call via ARI
        from apps.voip.services import originate_call

        success, error = originate_call(call_log, caller_ext)
        if not success:
            call_log.status = CallLog.Status.FAILED
            call_log.ended_at = timezone.now()
            call_log.metadata = {"error": error}
            call_log.save(update_fields=["status", "ended_at", "metadata", "updated_at"])
            return Response(
                {"detail": f"Failed to initiate call: {error}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        # Increment usage counter
        from apps.voip.services import increment_call_usage

        increment_call_usage(request.tenant)

        return Response(
            CallLogDetailSerializer(call_log).data,
            status=status.HTTP_201_CREATED,
        )


class CallHoldView(APIView):
    """
    Hold or resume an active call.

    POST /api/v1/voip/calls/<id>/hold/
    """

    permission_classes = [IsAuthenticated, IsTenantMember]

    def post(self, request, pk):
        try:
            call_log = CallLog.objects.get(id=pk, tenant=request.tenant)
        except CallLog.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        if not call_log.is_active:
            return Response(
                {"detail": "Call is not active."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from apps.voip.services import toggle_hold

        new_status, error = toggle_hold(call_log)
        if error:
            return Response(
                {"detail": error},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response(
            CallLogDetailSerializer(call_log).data,
            status=status.HTTP_200_OK,
        )


class CallTransferView(APIView):
    """
    Transfer an active call to another number or extension.

    POST /api/v1/voip/calls/<id>/transfer/
    {"target_number": "1002"}
    """

    permission_classes = [IsAuthenticated, IsTenantMember]

    def post(self, request, pk):
        serializer = CallActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            call_log = CallLog.objects.get(id=pk, tenant=request.tenant)
        except CallLog.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        if not call_log.is_active:
            return Response(
                {"detail": "Call is not active."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        target = serializer.validated_data.get("target_number")
        if not target:
            return Response(
                {"detail": "target_number is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from apps.voip.services import transfer_call

        success, error = transfer_call(call_log, target)
        if not success:
            return Response(
                {"detail": error},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response(
            {"detail": "Call transfer initiated."},
            status=status.HTTP_200_OK,
        )


class CallHangupView(APIView):
    """
    Hang up an active call.

    POST /api/v1/voip/calls/<id>/hangup/
    """

    permission_classes = [IsAuthenticated, IsTenantMember]

    def post(self, request, pk):
        try:
            call_log = CallLog.objects.get(id=pk, tenant=request.tenant)
        except CallLog.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        if not call_log.is_active:
            return Response(
                {"detail": "Call is not active."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from apps.voip.services import hangup_call

        success, error = hangup_call(call_log)
        if not success:
            return Response(
                {"detail": error},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response(
            {"detail": "Call ended."},
            status=status.HTTP_200_OK,
        )


# ---------------------------------------------------------------------------
# Call Recordings
# ---------------------------------------------------------------------------


class CallRecordingDownloadView(APIView):
    """
    Download a call recording file.

    GET /api/v1/voip/recordings/<id>/
    """

    permission_classes = [IsAuthenticated, IsTenantMember]

    def get(self, request, pk):
        try:
            recording = CallRecording.objects.select_related(
                "call_log"
            ).get(id=pk, tenant=request.tenant)
        except CallRecording.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        if not recording.file:
            return Response(
                {"detail": "Recording file not available."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return FileResponse(
            recording.file.open("rb"),
            content_type=recording.mime_type,
            as_attachment=True,
            filename=f"call-{recording.call_log_id}.wav",
        )


# ---------------------------------------------------------------------------
# Call Queues
# ---------------------------------------------------------------------------


class CallQueueViewSet(viewsets.ModelViewSet):
    """CRUD for call distribution queues."""

    serializer_class = CallQueueSerializer
    permission_classes = [IsAuthenticated, HasTenantPermission]
    permission_resource = "voip_queue"
    search_fields = ["name"]
    ordering = ["name"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return CallQueue.objects.none()
        return CallQueue.objects.prefetch_related("members").all()
