from rest_framework import serializers

from apps.notes.models import QuickNote


class QuickNoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuickNote
        fields = [
            "id",
            "content",
            "color",
            "is_pinned",
            "position",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
