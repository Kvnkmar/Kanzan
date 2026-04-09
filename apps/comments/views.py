"""
DRF ViewSets for comments and activity logs.

Both ViewSets support filtering by content_type and object_id, enabling
retrieval of comments/activity for any polymorphic entity.
"""

import logging

from django.contrib.contenttypes.models import ContentType
from django.db.models import Count
from django_filters import rest_framework as django_filters
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.comments.models import ActivityLog, Comment, CommentRead
from apps.comments.serializers import (
    ActivityLogSerializer,
    CommentCreateSerializer,
    CommentSerializer,
)
from apps.comments.services import log_activity

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


class CommentFilter(django_filters.FilterSet):
    """
    Filter comments by the target content object and visibility.

    Usage:
        ?content_type=tickets.ticket&object_id=<uuid>
        ?is_internal=true
        ?parent__isnull=true  (top-level only)
    """

    content_type = django_filters.CharFilter(method="filter_content_type")
    parent__isnull = django_filters.BooleanFilter(
        field_name="parent", lookup_expr="isnull"
    )

    class Meta:
        model = Comment
        fields = ["content_type", "object_id", "is_internal", "parent__isnull"]

    def filter_content_type(self, queryset, name, value):
        """Resolve 'app_label.model' to a ContentType filter."""
        try:
            app_label, model = value.strip().lower().split(".")
            ct = ContentType.objects.get(app_label=app_label, model=model)
            return queryset.filter(content_type=ct)
        except (ValueError, ContentType.DoesNotExist):
            return queryset.none()


class ActivityLogFilter(django_filters.FilterSet):
    """
    Filter activity logs by content object, actor, or action type.

    Usage:
        ?content_type=tickets.ticket&object_id=<uuid>
        ?actor=<user_uuid>
        ?action=created
    """

    content_type = django_filters.CharFilter(method="filter_content_type")
    created_at__gte = django_filters.IsoDateTimeFilter(
        field_name="created_at", lookup_expr="gte"
    )
    created_at__lte = django_filters.IsoDateTimeFilter(
        field_name="created_at", lookup_expr="lte"
    )

    class Meta:
        model = ActivityLog
        fields = ["content_type", "object_id", "actor", "action"]

    def filter_content_type(self, queryset, name, value):
        """Resolve 'app_label.model' to a ContentType filter."""
        try:
            app_label, model = value.strip().lower().split(".")
            ct = ContentType.objects.get(app_label=app_label, model=model)
            return queryset.filter(content_type=ct)
        except (ValueError, ContentType.DoesNotExist):
            return queryset.none()


# ---------------------------------------------------------------------------
# ViewSets
# ---------------------------------------------------------------------------


class CommentViewSet(viewsets.ModelViewSet):
    """
    CRUD operations for threaded comments.

    Supports filtering by content_type + object_id to retrieve comments
    for a specific entity. The `replies` action returns threaded replies
    for a given comment.

    List responses annotate each comment with `reply_count` for efficient
    UI rendering without N+1 queries.
    """

    permission_classes = [permissions.IsAuthenticated]
    filterset_class = CommentFilter
    search_fields = ["body"]
    ordering_fields = ["created_at"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return CommentCreateSerializer
        return CommentSerializer

    def get_queryset(self):
        qs = (
            Comment.objects.select_related("author", "content_type", "parent")
            .prefetch_related("mentions__mentioned_user")
            .annotate(reply_count=Count("replies"))
        )

        # Row-level filtering: non-admin users only see comments on their
        # own tickets (created by or assigned to them).
        user = self.request.user
        if not user.is_superuser:
            tenant = getattr(self.request, "tenant", None)
            if tenant:
                from apps.accounts.permissions import _get_membership

                membership = _get_membership(self.request, tenant)
                if membership and membership.role.hierarchy_level > 20:
                    from django.db.models import Q
                    from apps.tickets.models import Ticket

                    ticket_ct = ContentType.objects.get_for_model(Ticket)
                    visible_ticket_ids = (
                        Ticket.unscoped.filter(tenant=tenant)
                        .filter(Q(created_by=user) | Q(assignee=user))
                        .values_list("pk", flat=True)
                    )
                    qs = qs.filter(
                        content_type=ticket_ct,
                        object_id__in=visible_ticket_ids,
                    )
                    # Also hide internal notes from non-admin users
                    qs = qs.exclude(is_internal=True)

        return qs

    def perform_create(self, serializer):
        comment = serializer.save()

        # Log the comment creation as an activity
        log_activity(
            tenant=comment.tenant,
            actor=comment.author,
            content_object=comment.content_object,
            action=ActivityLog.Action.COMMENTED,
            description=f"Added a {'internal ' if comment.is_internal else ''}comment.",
            request=self.request,
        )

    def perform_update(self, serializer):
        old_body = serializer.instance.body
        comment = serializer.save()

        if old_body != comment.body:
            log_activity(
                tenant=comment.tenant,
                actor=self.request.user,
                content_object=comment,
                action=ActivityLog.Action.UPDATED,
                description="Edited a comment.",
                changes={"body": [old_body, comment.body]},
                request=self.request,
            )

    def perform_destroy(self, instance):
        # Log before deletion so the content_object reference is still valid.
        log_activity(
            tenant=instance.tenant,
            actor=self.request.user,
            content_object=instance.content_object,
            action=ActivityLog.Action.DELETED,
            description="Deleted a comment.",
            request=self.request,
        )
        instance.delete()

    @action(detail=True, methods=["get"], url_path="replies")
    def replies(self, request, pk=None):
        """
        Return paginated replies for a specific comment.

        GET /api/comments/<id>/replies/
        """
        comment = self.get_object()
        replies_qs = (
            Comment.objects.filter(parent=comment)
            .select_related("author", "content_type")
            .prefetch_related("mentions__mentioned_user")
            .annotate(reply_count=Count("replies"))
            .order_by("created_at")
        )

        page = self.paginate_queryset(replies_qs)
        if page is not None:
            serializer = CommentSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = CommentSerializer(replies_qs, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


    @action(detail=True, methods=["post"], url_path="mark-read")
    def mark_read(self, request, pk=None):
        """
        POST /api/v1/comments/comments/<id>/mark-read/

        Mark a single comment as read for the requesting user. Idempotent.
        """
        comment = self.get_object()
        CommentRead.objects.get_or_create(comment=comment, user=request.user)
        return Response({"status": "marked"}, status=status.HTTP_200_OK)


class ActivityLogViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only access to the activity audit trail.

    Supports filtering by content_type + object_id to view the full
    history of a specific entity, or by actor to see a user's actions.
    """

    serializer_class = ActivityLogSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_class = ActivityLogFilter
    search_fields = ["description"]
    ordering_fields = ["created_at"]
    ordering = ["-created_at"]

    def get_queryset(self):
        qs = ActivityLog.objects.select_related("actor", "content_type")

        # Row-level filtering: non-admin users only see activity on their
        # own tickets (created by or assigned to them).
        user = self.request.user
        if not user.is_superuser:
            tenant = getattr(self.request, "tenant", None)
            if tenant:
                from apps.accounts.permissions import _get_membership

                membership = _get_membership(self.request, tenant)
                if membership and membership.role.hierarchy_level > 20:
                    from django.db.models import Q
                    from apps.tickets.models import Ticket

                    ticket_ct = ContentType.objects.get_for_model(Ticket)
                    visible_ticket_ids = (
                        Ticket.unscoped.filter(tenant=tenant)
                        .filter(Q(created_by=user) | Q(assignee=user))
                        .values_list("pk", flat=True)
                    )
                    qs = qs.filter(
                        content_type=ticket_ct,
                        object_id__in=visible_ticket_ids,
                    )

        return qs
