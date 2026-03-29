"""
DRF serializers for file attachments.
"""

import logging

from django.contrib.contenttypes.models import ContentType
from rest_framework import serializers

from apps.attachments.models import Attachment
from apps.attachments.validators import validate_file_upload

logger = logging.getLogger(__name__)


class AttachmentSerializer(serializers.ModelSerializer):
    """
    Read serializer for attachments. Includes the download URL,
    human-readable size, and uploader info.
    """

    file_url = serializers.SerializerMethodField()
    size_display = serializers.CharField(read_only=True)
    content_type = serializers.SlugRelatedField(
        slug_field="model",
        read_only=True,
    )
    uploaded_by_name = serializers.SerializerMethodField()
    uploaded_by_email = serializers.EmailField(
        source="uploaded_by.email", read_only=True
    )

    class Meta:
        model = Attachment
        fields = [
            "id",
            "content_type",
            "object_id",
            "file_url",
            "original_name",
            "mime_type",
            "size_bytes",
            "size_display",
            "uploaded_by",
            "uploaded_by_name",
            "uploaded_by_email",
            "created_at",
        ]
        read_only_fields = fields

    def get_file_url(self, obj) -> str | None:
        request = self.context.get("request")
        if obj.file and request:
            return request.build_absolute_uri(obj.file.url)
        elif obj.file:
            return obj.file.url
        return None

    def get_uploaded_by_name(self, obj) -> str:
        return obj.uploaded_by.get_full_name()


class AttachmentUploadSerializer(serializers.Serializer):
    """
    Write serializer for uploading attachments. Handles file validation,
    MIME type detection, and content_type resolution.

    Expects multipart/form-data with:
        - file: The uploaded file
        - content_type: 'app_label.model' string
        - object_id: UUID of the target object
    """

    file = serializers.FileField()
    content_type = serializers.CharField(
        help_text="App label and model name in 'app_label.model' format (e.g. 'tickets.ticket').",
    )
    object_id = serializers.UUIDField()

    def validate_content_type(self, value: str) -> ContentType:
        """Resolve 'app_label.model' string to a ContentType instance."""
        try:
            app_label, model = value.strip().lower().split(".")
        except ValueError:
            raise serializers.ValidationError(
                "content_type must be in 'app_label.model' format (e.g. 'tickets.ticket')."
            )

        try:
            return ContentType.objects.get(app_label=app_label, model=model)
        except ContentType.DoesNotExist:
            raise serializers.ValidationError(
                f"Content type '{app_label}.{model}' does not exist."
            )

    def validate_file(self, file_obj):
        """
        Validate the uploaded file using python-magic based detection.
        Stores the detected MIME type on the serializer for use during create.
        """
        detected_mime = validate_file_upload(file_obj)
        # Stash the detected MIME so create() can use it without re-reading.
        self._detected_mime = detected_mime
        return file_obj

    def validate(self, attrs):
        """Verify the target object exists and belongs to the current tenant."""
        request = self.context.get("request")
        tenant = getattr(request, "tenant", None) if request else None
        ct = attrs["content_type"]
        object_id = attrs["object_id"]
        model_class = ct.model_class()

        if model_class is not None:
            target = model_class._default_manager.filter(pk=object_id).first()
            if target is None:
                raise serializers.ValidationError(
                    {"object_id": "Target object not found."}
                )
            if tenant and hasattr(target, "tenant_id") and target.tenant_id != tenant.pk:
                raise serializers.ValidationError(
                    {"object_id": "Target object does not belong to this tenant."}
                )
        return attrs

    def create(self, validated_data):
        """Create the Attachment record from validated upload data."""
        request = self.context["request"]
        file_obj = validated_data["file"]

        attachment = Attachment(
            tenant=request.tenant,
            content_type=validated_data["content_type"],
            object_id=validated_data["object_id"],
            file=file_obj,
            original_name=file_obj.name,
            mime_type=self._detected_mime,
            size_bytes=file_obj.size,
            uploaded_by=request.user,
        )
        attachment.save()

        logger.info(
            "Attachment uploaded via API: tenant=%s user=%s file=%s mime=%s size=%d",
            request.tenant.pk,
            request.user.pk,
            file_obj.name,
            self._detected_mime,
            file_obj.size,
        )

        return attachment
