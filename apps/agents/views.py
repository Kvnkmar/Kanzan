"""
DRF ViewSets for the agents app.

Provides CRUD endpoints for agent availability, including custom actions
for updating agent status and viewing workload statistics.
"""

import logging
from datetime import timedelta

from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.permissions import (
    HasTenantPermission,
    IsTenantAdminOrManager,
    IsTenantMember,
    _get_membership,
)
from apps.agents.models import AgentAvailability, AgentStatus, CustomAgentStatus
from apps.agents.serializers import (
    AgentAvailabilitySerializer,
    AgentStatusUpdateSerializer,
    AgentWorkloadSerializer,
    CustomAgentStatusSerializer,
)
from apps.agents.services import update_ticket_count
from apps.notifications.models import NotificationType
from apps.notifications.services import send_notification

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
        return AgentAvailability.objects.select_related("user", "custom_status").all()

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _status_display(tenant, key):
        """Resolve a status key (built-in slug or custom slug) to a human label."""
        builtin = dict(AgentStatus.choices).get(key)
        if builtin:
            return builtin
        if tenant is not None and key:
            cs = CustomAgentStatus.objects.filter(tenant=tenant, slug=key).first()
            if cs:
                return cs.label
        return key

    def _apply_status_payload(self, agent, request):
        """Apply a status-change request to an AgentAvailability instance.

        Mutates ``agent`` (status, custom_status, last_activity, optionally
        status_message). Returns the OLD status key (built-in slug or
        custom slug) so callers can notify peers about the transition.
        """
        serializer = AgentStatusUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data

        old_key = agent.custom_status.slug if agent.custom_status_id and agent.custom_status else agent.status

        update_fields = ["last_activity", "updated_at"]
        if "custom_status_id" in validated:
            new_custom_id = validated["custom_status_id"]
            if new_custom_id is None:
                agent.custom_status = None
                update_fields.append("custom_status")
            else:
                try:
                    cs = CustomAgentStatus.objects.get(
                        pk=new_custom_id, tenant=request.tenant, is_active=True,
                    )
                except CustomAgentStatus.DoesNotExist:
                    raise ValidationError({"custom_status_id": "Status not found or inactive."})
                agent.custom_status = cs
                update_fields.append("custom_status")
        if "status" in validated:
            agent.status = validated["status"]
            update_fields.append("status")
            # Picking a built-in always clears any active custom status
            if agent.custom_status_id:
                agent.custom_status = None
                if "custom_status" not in update_fields:
                    update_fields.append("custom_status")

        agent.last_activity = timezone.now()
        agent.save(update_fields=update_fields)
        return old_key

    def _notify_status_change(self, user, tenant, old_status, new_status):
        """Notify all other tenant members when an agent changes status."""
        if old_status == new_status:
            return

        from apps.accounts.models import TenantMembership

        display_name = user.get_full_name() or user.email
        old_display = self._status_display(tenant, old_status)
        new_display = self._status_display(tenant, new_status)

        members = TenantMembership.objects.filter(
            tenant=tenant, is_active=True,
        ).exclude(user=user).select_related("user")

        for membership in members:
            send_notification(
                tenant=tenant,
                recipient=membership.user,
                notification_type=NotificationType.AGENT_STATUS_CHANGE,
                title=f"{display_name} is now {new_display}",
                body=f"Status changed from {old_display} to {new_display}",
                data={
                    "user_id": str(user.id),
                    "old_status": old_status,
                    "new_status": new_status,
                    "url": "/agents/",
                },
            )

    # ------------------------------------------------------------------
    # Custom actions
    # ------------------------------------------------------------------

    @action(detail=True, methods=["post"], url_path="set-status")
    def set_status(self, request, pk=None):
        """
        Update the agent's availability status.

        POST /agents/{id}/set-status/
        {"status": "online"|"offline"|...}  OR  {"custom_status_id": "<uuid>"}
        """
        agent = self.get_object()
        old_key = self._apply_status_payload(agent, request)
        new_key = (
            agent.custom_status.slug
            if agent.custom_status_id and agent.custom_status
            else agent.status
        )

        logger.info(
            "Agent %s status changed: %s -> %s",
            agent.user.email,
            old_key,
            new_key,
        )

        self._notify_status_change(agent.user, request.tenant, old_key, new_key)

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
            old_key = self._apply_status_payload(agent, request)
            new_key = (
                agent.custom_status.slug
                if agent.custom_status_id and agent.custom_status
                else agent.status
            )

            logger.info(
                "Agent %s toggled status: %s -> %s",
                agent.user.email,
                old_key,
                new_key,
            )

            self._notify_status_change(
                request.user, request.tenant, old_key, new_key,
            )

        elif request.method == "PATCH":
            update_fields = ["updated_at"]
            old_key = (
                agent.custom_status.slug
                if agent.custom_status_id and agent.custom_status
                else agent.status
            )
            if "max_concurrent_tickets" in request.data:
                val = int(request.data["max_concurrent_tickets"])
                agent.max_concurrent_tickets = max(1, min(val, 50))
                update_fields.append("max_concurrent_tickets")
            if "status" in request.data:
                agent.status = request.data["status"]
                agent.last_activity = timezone.now()
                update_fields.extend(["status", "last_activity"])
                # Built-in pick clears any custom status
                if agent.custom_status_id:
                    agent.custom_status = None
                    update_fields.append("custom_status")
            if "custom_status_id" in request.data:
                raw = request.data["custom_status_id"]
                if raw in (None, "", "null"):
                    agent.custom_status = None
                else:
                    try:
                        cs = CustomAgentStatus.objects.get(
                            pk=raw, tenant=request.tenant, is_active=True,
                        )
                    except CustomAgentStatus.DoesNotExist:
                        raise ValidationError({"custom_status_id": "Status not found or inactive."})
                    agent.custom_status = cs
                agent.last_activity = timezone.now()
                if "custom_status" not in update_fields:
                    update_fields.append("custom_status")
                if "last_activity" not in update_fields:
                    update_fields.append("last_activity")
            if "status_message" in request.data:
                agent.status_message = request.data["status_message"] or ""
                update_fields.append("status_message")
            if "working_hours" in request.data:
                agent.working_hours = request.data["working_hours"] or {}
                update_fields.append("working_hours")
            if "auto_away_outside_hours" in request.data:
                agent.auto_away_outside_hours = bool(request.data["auto_away_outside_hours"])
                update_fields.append("auto_away_outside_hours")
            agent.save(update_fields=update_fields)

            if "status" in request.data or "custom_status_id" in request.data:
                new_key = (
                    agent.custom_status.slug
                    if agent.custom_status_id and agent.custom_status
                    else agent.status
                )
                self._notify_status_change(
                    request.user, request.tenant, old_key, new_key,
                )

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

        # Admins can opt in to seeing deactivated members so they can
        # reactivate or otherwise manage them. Non-admins only ever see
        # active members.
        include_deactivated = (
            request.query_params.get("include_deactivated", "").lower()
            in ("1", "true", "yes")
        )
        admin_membership = _get_membership(request, tenant)
        is_admin = (
            admin_membership is not None
            and admin_membership.effective_role.hierarchy_level <= 10
        )

        membership_qs = TenantMembership.objects.filter(tenant=tenant)
        if not (include_deactivated and is_admin):
            membership_qs = membership_qs.filter(is_active=True)
        memberships = membership_qs.select_related(
            "user", "role", "temporary_role", "temporary_role_granted_by",
        ).prefetch_related("temporary_permissions")

        # Build a lookup of existing availability records
        availability_map = {}
        for agent in self.get_queryset():
            availability_map[agent.user_id] = agent

        data = []
        for m in memberships:
            agent = availability_map.get(m.user_id)
            temp_active = m.has_active_temporary_role
            granter = m.temporary_role_granted_by
            custom_status_payload = None
            # Deactivated members surface as "deactivated" regardless of
            # any cached availability status, since they no longer receive
            # work assignments.
            if not m.is_active:
                row_status = "deactivated"
                row_status_display = "Deactivated"
            elif agent and agent.custom_status_id and agent.custom_status:
                row_status = agent.custom_status.slug
                row_status_display = agent.custom_status.label
                custom_status_payload = {
                    "id": str(agent.custom_status.id),
                    "slug": agent.custom_status.slug,
                    "label": agent.custom_status.label,
                    "color": agent.custom_status.color,
                    "color_hex": agent.custom_status.color_hex,
                }
            elif agent:
                row_status = agent.status
                row_status_display = agent.get_status_display()
            else:
                row_status = "offline"
                row_status_display = "Offline"

            data.append({
                "id": str(m.id),
                "membership_id": str(m.id),
                "user_id": str(m.user_id),
                "user_name": m.user.get_full_name() or m.user.email,
                "user_email": m.user.email,
                "is_active": m.is_active,
                "role": m.role.name if m.role else "Unknown",
                "permanent_role": m.role.name if m.role else "Unknown",
                "permanent_role_id": str(m.role.id) if m.role else None,
                "temporary_role": m.temporary_role.name if temp_active else None,
                "temporary_role_id": str(m.temporary_role_id) if temp_active else None,
                "temporary_role_expires_at": (
                    m.temporary_role_expires_at.isoformat()
                    if temp_active else None
                ),
                "temporary_role_granted_by": (
                    (granter.get_full_name() or granter.email)
                    if temp_active and granter else None
                ),
                "temporary_permission_ids": (
                    [str(p.id) for p in m.temporary_permissions.all()]
                    if temp_active else []
                ),
                "status": row_status,
                "status_display": row_status_display,
                "custom_status": custom_status_payload,
                "current_ticket_count": agent.current_ticket_count if agent else 0,
                "max_concurrent_tickets": agent.max_concurrent_tickets if agent else 10,
                "last_activity": agent.last_activity.isoformat() if agent and agent.last_activity else None,
            })

        return Response(data, status=status.HTTP_200_OK)

    @action(
        detail=False,
        methods=["get"],
        url_path="assignable-roles",
        permission_classes=[IsAuthenticated],
    )
    def assignable_roles(self, request):
        """
        Roles that the current admin can grant as a temporary role.

        Admin-only. Returns all roles defined in this tenant.

        GET /agents/assignable-roles/
        """
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response([], status=status.HTTP_200_OK)

        membership = _get_membership(request, tenant)
        if membership is None or membership.effective_role.hierarchy_level > 10:
            raise PermissionDenied("Only admins can list assignable roles.")

        from apps.accounts.models import Role

        roles = Role.objects.filter(tenant=tenant).order_by("hierarchy_level", "name")
        data = [
            {
                "id": str(r.id),
                "name": r.name,
                "slug": r.slug,
                "hierarchy_level": r.hierarchy_level,
            }
            for r in roles
        ]
        return Response(data, status=status.HTTP_200_OK)

    @action(
        detail=False,
        methods=["get"],
        url_path=r"role-permissions/(?P<role_id>[0-9a-f-]+)",
        permission_classes=[IsAuthenticated],
    )
    def role_permissions(self, request, role_id=None):
        """
        Permissions attached to a specific role, grouped by resource.

        Admin-only. Used by the grant-temp-role modal to render the
        per-grant access checklist. Returns an ordered list of resource
        groups, each with its permissions.

        GET /agents/role-permissions/{role_id}/
        """
        tenant = getattr(request, "tenant", None)
        if not tenant:
            raise PermissionDenied("Tenant required.")

        membership = _get_membership(request, tenant)
        if membership is None or membership.effective_role.hierarchy_level > 10:
            raise PermissionDenied("Only admins can inspect role permissions.")

        from apps.accounts.models import Role

        try:
            role = Role.objects.prefetch_related("permissions").get(
                id=role_id, tenant=tenant,
            )
        except Role.DoesNotExist:
            raise ValidationError("Role not found in this tenant.")

        # Group permissions by resource for nicer rendering. Preserve
        # resource order by first-seen for determinism.
        groups = {}
        order = []
        for perm in role.permissions.all().order_by("resource", "action"):
            if perm.resource not in groups:
                groups[perm.resource] = []
                order.append(perm.resource)
            groups[perm.resource].append({
                "id": str(perm.id),
                "codename": perm.codename,
                "name": perm.name,
                "action": perm.action,
            })

        data = {
            "role": {
                "id": str(role.id),
                "name": role.name,
                "slug": role.slug,
                "hierarchy_level": role.hierarchy_level,
            },
            "groups": [
                {"resource": r, "permissions": groups[r]} for r in order
            ],
        }
        return Response(data, status=status.HTTP_200_OK)

    @action(
        detail=False,
        methods=["post"],
        url_path="grant-temp-role",
        permission_classes=[IsAuthenticated],
    )
    def grant_temp_role(self, request):
        """
        Grant a temporary role to a tenant member. Admin-only.

        POST /agents/grant-temp-role/
        {
            "user_id": "<uuid>",
            "role_id": "<uuid>",
            "duration_hours": 8,          # OR "expires_at": ISO datetime
            "permission_ids": ["<uuid>", ...]   # optional access subset
        }

        When `permission_ids` is omitted or empty, the temporary role's
        full permission set applies. When provided, only the supplied
        permissions (intersected with the role's perms) take effect
        during the grant window.
        """
        tenant = getattr(request, "tenant", None)
        if not tenant:
            raise PermissionDenied("Tenant required.")

        admin_membership = _get_membership(request, tenant)
        if admin_membership is None or admin_membership.effective_role.hierarchy_level > 10:
            raise PermissionDenied("Only admins can grant temporary roles.")

        from apps.accounts.models import Permission, Role, TenantMembership

        user_id = request.data.get("user_id")
        role_id = request.data.get("role_id")
        if not user_id or not role_id:
            raise ValidationError("user_id and role_id are required.")

        try:
            target = TenantMembership.objects.select_related("role").get(
                user_id=user_id, tenant=tenant, is_active=True,
            )
        except TenantMembership.DoesNotExist:
            raise ValidationError("Target user is not an active member of this tenant.")

        if target.user_id == request.user.pk:
            raise ValidationError("You cannot grant a temporary role to yourself.")

        try:
            role = Role.objects.prefetch_related("permissions").get(
                id=role_id, tenant=tenant,
            )
        except Role.DoesNotExist:
            raise ValidationError("Role not found in this tenant.")

        # Resolve optional permission subset. Silently drop ids that
        # aren't in the role — the admin's checklist should already
        # restrict to role perms, but be defensive against tampering.
        permission_ids = request.data.get("permission_ids") or []
        if not isinstance(permission_ids, list):
            raise ValidationError("permission_ids must be a list of UUIDs.")
        permission_objs = []
        if permission_ids:
            role_perm_ids = set(
                str(pid) for pid in role.permissions.values_list("id", flat=True)
            )
            filtered_ids = [pid for pid in permission_ids if str(pid) in role_perm_ids]
            if not filtered_ids:
                raise ValidationError(
                    "None of the supplied permissions belong to the chosen role."
                )
            permission_objs = list(Permission.objects.filter(id__in=filtered_ids))

        # Resolve expiry
        expires_at_raw = request.data.get("expires_at")
        duration_hours = request.data.get("duration_hours")
        if expires_at_raw:
            from django.utils.dateparse import parse_datetime

            expires_at = parse_datetime(expires_at_raw)
            if expires_at is None:
                raise ValidationError("expires_at must be a valid ISO datetime.")
            if timezone.is_naive(expires_at):
                expires_at = timezone.make_aware(expires_at, timezone.get_current_timezone())
        elif duration_hours is not None:
            try:
                hours = float(duration_hours)
            except (TypeError, ValueError):
                raise ValidationError("duration_hours must be a number.")
            if hours <= 0 or hours > 24 * 30:
                raise ValidationError("duration_hours must be between 0 and 720 (30 days).")
            expires_at = timezone.now() + timedelta(hours=hours)
        else:
            raise ValidationError("Provide either duration_hours or expires_at.")

        if expires_at <= timezone.now():
            raise ValidationError("Expiry must be in the future.")

        target.temporary_role = role
        target.temporary_role_expires_at = expires_at
        target.temporary_role_granted_by = request.user
        target.temporary_role_granted_at = timezone.now()
        target.save(update_fields=[
            "temporary_role",
            "temporary_role_expires_at",
            "temporary_role_granted_by",
            "temporary_role_granted_at",
        ])
        # Apply the optional access subset. set() replaces the M2M
        # atomically; passing [] clears it so the role's full perm set
        # applies (current behaviour for backwards-compatible grants).
        target.temporary_permissions.set(permission_objs)

        logger.info(
            "Temp role granted: %s -> %s (until %s, %d perm subset) by %s",
            target.user.email,
            role.name,
            expires_at.isoformat(),
            len(permission_objs),
            request.user.email,
        )

        # Notify the recipient so they know about the elevation
        try:
            send_notification(
                tenant=tenant,
                recipient=target.user,
                notification_type=NotificationType.AGENT_STATUS_CHANGE,
                title=f"You have been granted the {role.name} role temporarily",
                body=f"This temporary role expires at {expires_at.strftime('%Y-%m-%d %H:%M')}.",
                data={
                    "role": role.name,
                    "expires_at": expires_at.isoformat(),
                    "url": "/agents/",
                },
            )
        except Exception:
            logger.exception("Failed to notify recipient of temp role grant")

        return Response({
            "user_id": str(target.user_id),
            "permanent_role": target.role.name,
            "temporary_role": role.name,
            "temporary_role_id": str(role.id),
            "expires_at": expires_at.isoformat(),
            "temporary_permission_ids": [str(p.id) for p in permission_objs],
        }, status=status.HTTP_200_OK)

    @action(
        detail=False,
        methods=["post"],
        url_path="revoke-temp-role",
        permission_classes=[IsAuthenticated],
    )
    def revoke_temp_role(self, request):
        """
        Revoke an active temporary role. Admin-only.

        POST /agents/revoke-temp-role/
        {"user_id": "<uuid>"}
        """
        tenant = getattr(request, "tenant", None)
        if not tenant:
            raise PermissionDenied("Tenant required.")

        admin_membership = _get_membership(request, tenant)
        if admin_membership is None or admin_membership.effective_role.hierarchy_level > 10:
            raise PermissionDenied("Only admins can revoke temporary roles.")

        from apps.accounts.models import TenantMembership

        user_id = request.data.get("user_id")
        if not user_id:
            raise ValidationError("user_id is required.")

        try:
            target = TenantMembership.objects.select_related(
                "role", "temporary_role",
            ).get(user_id=user_id, tenant=tenant, is_active=True)
        except TenantMembership.DoesNotExist:
            raise ValidationError("Target user is not an active member of this tenant.")

        if target.temporary_role_id is None:
            raise ValidationError("This member does not have a temporary role.")

        prior_temp = target.temporary_role.name if target.temporary_role else None
        target.temporary_role = None
        target.temporary_role_expires_at = None
        target.temporary_role_granted_by = None
        target.temporary_role_granted_at = None
        target.save(update_fields=[
            "temporary_role",
            "temporary_role_expires_at",
            "temporary_role_granted_by",
            "temporary_role_granted_at",
        ])
        target.temporary_permissions.clear()

        logger.info(
            "Temp role revoked: %s (was %s) by %s",
            target.user.email, prior_temp, request.user.email,
        )

        return Response({
            "user_id": str(target.user_id),
            "permanent_role": target.role.name,
            "temporary_role": None,
        }, status=status.HTTP_200_OK)

    @action(
        detail=False,
        methods=["post"],
        url_path="reactivate",
        permission_classes=[IsAuthenticated],
    )
    def reactivate(self, request):
        """
        Reactivate a deactivated tenant member. Admin-only.

        POST /agents/reactivate/
        {"user_id": "<uuid>"}
        """
        tenant = getattr(request, "tenant", None)
        if not tenant:
            raise PermissionDenied("Tenant required.")

        admin_membership = _get_membership(request, tenant)
        if admin_membership is None or admin_membership.effective_role.hierarchy_level > 10:
            raise PermissionDenied("Only admins can reactivate members.")

        from apps.accounts.models import TenantMembership

        user_id = request.data.get("user_id")
        if not user_id:
            raise ValidationError("user_id is required.")

        try:
            target = TenantMembership.objects.select_related("role", "user").get(
                user_id=user_id, tenant=tenant,
            )
        except TenantMembership.DoesNotExist:
            raise ValidationError("No membership found for that user in this tenant.")

        if target.is_active:
            raise ValidationError("This member is already active.")

        target.is_active = True
        target.save(update_fields=["is_active"])

        logger.info(
            "Member reactivated: %s by %s", target.user.email, request.user.email,
        )

        return Response({
            "user_id": str(target.user_id),
            "is_active": True,
        }, status=status.HTTP_200_OK)

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


class CustomAgentStatusViewSet(viewsets.ModelViewSet):
    """
    CRUD for tenant-curated custom availability statuses.

    - List/retrieve: any authenticated tenant member (so the navbar
      dropdown can render the curated list).
    - Create/update/delete: Admin or Manager (hierarchy_level <= 20).
    """

    serializer_class = CustomAgentStatusSerializer
    ordering_fields = ["order", "label", "created_at"]
    ordering = ["order", "label"]
    pagination_class = None

    def get_queryset(self):
        qs = CustomAgentStatus.objects.all()
        # Non-admins only see active statuses (they're the only ones selectable)
        params = self.request.query_params
        if params.get("all", "").lower() not in ("1", "true", "yes"):
            qs = qs.filter(is_active=True)
        return qs

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated(), IsTenantMember()]
        return [IsAuthenticated(), IsTenantAdminOrManager()]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
