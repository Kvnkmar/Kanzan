"""
DRF serializers for the VoIP app.

Provides read/write serializers for VoIP settings, extensions, call logs,
recordings, and call queues. SIP passwords are write-only.
"""

from rest_framework import serializers

from apps.voip.models import (
    CallLog,
    CallQueue,
    CallRecording,
    Extension,
    VoIPSettings,
)


# ---------------------------------------------------------------------------
# VoIP Settings
# ---------------------------------------------------------------------------


class VoIPSettingsSerializer(serializers.ModelSerializer):
    """Full read/write serializer for per-tenant VoIP configuration."""

    class Meta:
        model = VoIPSettings
        fields = [
            "id",
            "asterisk_host",
            "asterisk_ari_port",
            "asterisk_wss_port",
            "ari_username",
            "ari_password",
            "stun_server",
            "turn_server",
            "turn_username",
            "turn_password",
            "default_caller_id",
            "pjsip_context",
            "recording_enabled",
            "voicemail_enabled",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
        extra_kwargs = {
            "ari_password": {"write_only": True},
            "turn_password": {"write_only": True},
        }


# ---------------------------------------------------------------------------
# Extensions
# ---------------------------------------------------------------------------


class ExtensionSerializer(serializers.ModelSerializer):
    """Read serializer for extensions with user details."""

    user_email = serializers.EmailField(source="user.email", read_only=True)
    user_name = serializers.SerializerMethodField()

    class Meta:
        model = Extension
        fields = [
            "id",
            "user",
            "user_email",
            "user_name",
            "extension_number",
            "sip_username",
            "caller_id_name",
            "caller_id_number",
            "is_active",
            "registered_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "sip_username",
            "registered_at",
            "created_at",
            "updated_at",
        ]

    def get_user_name(self, obj):
        return obj.user.get_full_name()


class ExtensionCreateSerializer(serializers.ModelSerializer):
    """Write serializer for creating/updating extensions."""

    class Meta:
        model = Extension
        fields = [
            "user",
            "extension_number",
            "sip_username",
            "sip_password",
            "caller_id_name",
            "caller_id_number",
            "is_active",
        ]
        extra_kwargs = {
            "sip_password": {"write_only": True},
        }


class SIPCredentialsSerializer(serializers.Serializer):
    """Read-only serializer for SIP registration credentials."""

    sip_uri = serializers.CharField()
    sip_password = serializers.CharField()
    wss_url = serializers.CharField()
    stun_servers = serializers.ListField(child=serializers.CharField())
    turn_servers = serializers.ListField(child=serializers.DictField())
    extension_number = serializers.CharField()
    caller_id_name = serializers.CharField()


# ---------------------------------------------------------------------------
# Call Logs
# ---------------------------------------------------------------------------


class CallLogListSerializer(serializers.ModelSerializer):
    """Compact serializer for call log list views."""

    caller_extension_number = serializers.CharField(
        source="caller_extension.extension_number",
        read_only=True,
        default=None,
    )
    callee_extension_number = serializers.CharField(
        source="callee_extension.extension_number",
        read_only=True,
        default=None,
    )
    contact_name = serializers.SerializerMethodField()
    has_recording = serializers.BooleanField(
        source="recording",
        read_only=True,
        default=False,
    )

    class Meta:
        model = CallLog
        fields = [
            "id",
            "direction",
            "status",
            "caller_number",
            "callee_number",
            "caller_extension_number",
            "callee_extension_number",
            "contact",
            "contact_name",
            "ticket",
            "started_at",
            "duration_seconds",
            "has_recording",
        ]

    def get_contact_name(self, obj):
        if obj.contact:
            return f"{obj.contact.first_name} {obj.contact.last_name}"
        return None


class CallLogDetailSerializer(serializers.ModelSerializer):
    """Full serializer for call log detail views."""

    caller_extension_number = serializers.CharField(
        source="caller_extension.extension_number",
        read_only=True,
        default=None,
    )
    callee_extension_number = serializers.CharField(
        source="callee_extension.extension_number",
        read_only=True,
        default=None,
    )
    contact_name = serializers.SerializerMethodField()
    recording = serializers.SerializerMethodField()

    class Meta:
        model = CallLog
        fields = [
            "id",
            "asterisk_channel_id",
            "direction",
            "status",
            "caller_extension",
            "callee_extension",
            "caller_extension_number",
            "callee_extension_number",
            "caller_number",
            "callee_number",
            "contact",
            "contact_name",
            "ticket",
            "started_at",
            "answered_at",
            "ended_at",
            "duration_seconds",
            "hold_duration_seconds",
            "notes",
            "metadata",
            "recording",
            "created_at",
        ]

    def get_contact_name(self, obj):
        if obj.contact:
            return f"{obj.contact.first_name} {obj.contact.last_name}"
        return None

    def get_recording(self, obj):
        try:
            rec = obj.recording
        except CallRecording.DoesNotExist:
            return None
        return {
            "id": str(rec.id),
            "duration_seconds": rec.duration_seconds,
            "size_bytes": rec.size_bytes,
            "mime_type": rec.mime_type,
        }


class CallLogUpdateSerializer(serializers.ModelSerializer):
    """Serializer for updating call notes and linking to contact/ticket."""

    class Meta:
        model = CallLog
        fields = ["notes", "contact", "ticket"]


# ---------------------------------------------------------------------------
# Call Initiation
# ---------------------------------------------------------------------------


class InitiateCallSerializer(serializers.Serializer):
    """Serializer for initiating an outbound call."""

    callee_number = serializers.CharField(max_length=50)
    contact_id = serializers.UUIDField(required=False, allow_null=True)
    ticket_id = serializers.UUIDField(required=False, allow_null=True)


class CallActionSerializer(serializers.Serializer):
    """Serializer for call hold/transfer actions."""

    target_number = serializers.CharField(
        max_length=50,
        required=False,
        help_text="Target number for call transfer.",
    )


# ---------------------------------------------------------------------------
# Call Recordings
# ---------------------------------------------------------------------------


class CallRecordingSerializer(serializers.ModelSerializer):
    """Read serializer for call recordings."""

    call_id = serializers.UUIDField(source="call_log.id", read_only=True)

    class Meta:
        model = CallRecording
        fields = [
            "id",
            "call_id",
            "duration_seconds",
            "size_bytes",
            "mime_type",
            "created_at",
        ]


# ---------------------------------------------------------------------------
# Call Queues
# ---------------------------------------------------------------------------


class CallQueueSerializer(serializers.ModelSerializer):
    """Read/write serializer for call queues."""

    member_count = serializers.SerializerMethodField()

    class Meta:
        model = CallQueue
        fields = [
            "id",
            "name",
            "strategy",
            "members",
            "member_count",
            "timeout_seconds",
            "max_wait_seconds",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_member_count(self, obj):
        return obj.members.count()
