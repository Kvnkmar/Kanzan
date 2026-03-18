"""
DRF ViewSets for the agents app.

Provides CRUD endpoints for agent availability, including custom actions
for updating agent status and viewing workload statistics.
"""

import logging

from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.permissions import HasTenantPermission
from apps.agents.models import AgentAvailability, AgentStatus
from apps.agents.serializers import (
    AgentAvailabilitySerializer,
    AgentStatusUpdateSerializer,
    AgentWorkloadSerializer,
)
from apps.agents.services import update_ticket_count

logger = logging.getLogger(__name__)


class AgentAvailabilityViewSet(viewsets.ModelViewSet):
    """
    CRUD for agent availability records.

    Agents can update their own status; admins can view and manage all
    availability records within the tenant.

    Custom actions:
        - ``set_status``: Update the agent's availability status.
        - ``workload``: Return workload statistics for all agents.
    """

    serializer_class = AgentAvailabilitySerializer
    permission_classes = [IsAuthenticated, HasTenantPermission]
    permission_resource = "agent"
    search_fields = ["user__email", "user__first_name", "user__last_name"]
    ordering_fields = ["status", "current_ticket_count", "last_activity", "created_at"]
    ordering = ["-last_activity"]

    def get_queryset(self):
        return AgentAvailability.objects.select_related("user").all()

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    # ------------------------------------------------------------------
    # Custom actions
    # ------------------------------------------------------------------

    @action(detail=True, methods=["post"], url_path="set-status")
    def set_status(self, request, pk=None):
        """
        Update the agent's availability status.

        POST /agents/{id}/set-status/
        {"status": "online"|"offline"}
        """
        agent = self.get_object()
        serializer = AgentStatusUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        new_status = serializer.validated_data["status"]
        old_status = agent.status

        agent.status = new_status
        agent.last_activity = timezone.now()
        agent.save(update_fields=["status", "last_activity", "updated_at"])

        logger.info(
            "Agent %s status changed: %s -> %s",
            agent.user.email,
            old_status,
            new_status,
        )

        return Response(
            AgentAvailabilitySerializer(agent, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )

    @action(
        detail=False,
        methods=["get", "post", "patch"],
        url_path="my-status",
        permission_classes=[IsAuthenticated],
    )
    def my_status(self, request):
        """
        Get or update the current user's availability status and settings.

        GET   /agents/my-status/  → returns current availability (creates if missing)
        POST  /agents/my-status/  → {"status": "online"|"offline"} updates status
        PATCH /agents/my-status/  → {"max_concurrent_tickets": 5, ...} updates settings
        """
        agent, created = AgentAvailability.objects.get_or_create(
            user=request.user,
            defaults={"status": AgentStatus.OFFLINE},
        )

        if request.method == "POST":
            serializer = AgentStatusUpdateSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)

            new_status = serializer.validated_data["status"]
            old_status = agent.status

            agent.status = new_status
            agent.last_activity = timezone.now()
            agent.save(update_fields=["status", "last_activity", "updated_at"])

            logger.info(
                "Agent %s toggled status: %s -> %s",
                agent.user.email,
                old_status,
                new_status,
            )

        elif request.method == "PATCH":
            update_fields = ["updated_at"]
            if "max_concurrent_tickets" in request.data:
                val = int(request.data["max_concurrent_tickets"])
                agent.max_concurrent_tickets = max(1, min(val, 50))
                update_fields.append("max_concurrent_tickets")
            if "status" in request.data:
                agent.status = request.data["status"]
                agent.last_activity = timezone.now()
                update_fields.extend(["status", "last_activity"])
            if "status_message" in request.data:
                agent.status_message = request.data["status_message"] or ""
                update_fields.append("status_message")
            agent.save(update_fields=update_fields)

        return Response(
            AgentAvailabilitySerializer(agent, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"], url_path="all-members")
    def all_members(self, request):
        """
        Return all tenant members with their availability status.

        Combines TenantMembership data with AgentAvailability records
        so every member appears (defaulting to offline if no record).

        GET /agents/all-members/
        """
        from apps.accounts.models import TenantMembership

        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response([], status=status.HTTP_200_OK)

        memberships = (
            TenantMembership.objects.filter(tenant=tenant, is_active=True)
            .select_related("user", "role")
        )

        # Build a lookup of existing availability records
        availability_map = {}
        for agent in self.get_queryset():
            availability_map[agent.user_id] = agent

        data = []
        for m in memberships:
            agent = availability_map.get(m.user_id)
            data.append({
                "id": str(m.id),
                "user_id": str(m.user_id),
                "user_name": m.user.get_full_name() or m.user.email,
                "user_email": m.user.email,
                "role": m.role.name if m.role else "Unknown",
                "status": agent.status if agent else "offline",
                "status_display": agent.get_status_display() if agent else "Offline",
                "current_ticket_count": agent.current_ticket_count if agent else 0,
                "max_concurrent_tickets": agent.max_concurrent_tickets if agent else 10,
                "last_activity": agent.last_activity.isoformat() if agent and agent.last_activity else None,
            })

        return Response(data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="online")
    def online(self, request):
        """
        Return a list of agents currently online in this tenant.

        GET /agents/online/
        """
        online_agents = self.get_queryset().filter(status=AgentStatus.ONLINE)
        data = []
        for agent in online_agents:
            data.append(
                {
                    "id": str(agent.id),
                    "user_name": agent.user.get_full_name() or agent.user.email,
                    "user_email": agent.user.email,
                    "status": agent.status,
                    "status_display": agent.get_status_display(),
                }
            )
        return Response(
            {"count": len(data), "agents": data},
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"], url_path="workload")
    def workload(self, request):
        """
        Return workload statistics for all agents in the tenant.

        Recalculates each agent's ticket count from actual assigned open
        tickets before returning the data.

        GET /agents/workload/
        """
        agents = self.get_queryset()

        # Recalculate ticket counts to ensure accuracy.
        for agent in agents:
            update_ticket_count(agent)

        data = []
        for agent in agents:
            data.append(
                {
                    "user_id": agent.user_id,
                    "user_email": agent.user.email,
                    "user_name": agent.user.get_full_name(),
                    "status": agent.status,
                    "current_ticket_count": agent.current_ticket_count,
                    "max_concurrent_tickets": agent.max_concurrent_tickets,
                    "remaining_capacity": agent.remaining_capacity,
                    "is_available": agent.is_available,
                }
            )

        serializer = AgentWorkloadSerializer(data, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
