"""
DRF serializers for the agents app.

Provides serializers for AgentAvailability with both read and write
representations, including a dedicated status-update serializer.
"""

from rest_framework import serializers

from apps.agents.models import AgentAvailability, AgentStatus


class AgentAvailabilitySerializer(serializers.ModelSerializer):
    """Full read serializer for agent availability records."""

    user_email = serializers.EmailField(source="user.email", read_only=True)
    user_name = serializers.SerializerMethodField()
    is_available = serializers.BooleanField(read_only=True)
    remaining_capacity = serializers.IntegerField(read_only=True)

    class Meta:
        model = AgentAvailability
        fields = [
            "id",
            "user",
            "user_email",
            "user_name",
            "status",
            "status_message",
            "max_concurrent_tickets",
            "current_ticket_count",
            "last_activity",
            "is_available",
            "remaining_capacity",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "user",
            "current_ticket_count",
            "last_activity",
            "created_at",
            "updated_at",
        ]

    def get_user_name(self, obj):
        return obj.user.get_full_name()


class AgentStatusUpdateSerializer(serializers.Serializer):
    """Serializer for the set_status action."""

    status = serializers.ChoiceField(choices=AgentStatus.choices)


class AgentWorkloadSerializer(serializers.Serializer):
    """Read-only serializer for the workload action response."""

    user_id = serializers.UUIDField()
    user_email = serializers.EmailField()
    user_name = serializers.CharField()
    status = serializers.CharField()
    current_ticket_count = serializers.IntegerField()
    max_concurrent_tickets = serializers.IntegerField()
    remaining_capacity = serializers.IntegerField()
    is_available = serializers.BooleanField()
