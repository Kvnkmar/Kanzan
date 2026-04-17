from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import Count, Exists, OuterRef, Subquery
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.permissions import IsTenantAdminOrManager, IsTenantMember
from apps.comments.models import Comment
from apps.newsfeed.models import (
    NewsPost,
    NewsPostReaction,
    NewsPostRead,
    ReactionType,
)
from apps.newsfeed.serializers import NewsPostSerializer


class NewsPostViewSet(viewsets.ModelViewSet):
    """
    News feed for the tenant dashboard.

    - All authenticated tenant members can list/retrieve published posts.
    - Only Admin/Manager can create, update, or delete posts.
    - All members can react, mark-read, and view unread count.
    """

    serializer_class = NewsPostSerializer

    def get_permissions(self):
        if self.action in ("list", "retrieve", "react", "mark_read", "mark_all_read", "unread_count"):
            return [IsAuthenticated(), IsTenantMember()]
        return [IsAuthenticated(), IsTenantAdminOrManager()]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return NewsPost.objects.none()

        qs = NewsPost.objects.select_related("author")
        user = self.request.user

        # Annotate read status
        qs = qs.annotate(
            is_read=Exists(
                NewsPostRead.objects.filter(
                    post=OuterRef("pk"), user=user
                )
            )
        )

        # Annotate comment count via GenericFK
        ct = ContentType.objects.get_for_model(NewsPost)
        comment_count_sq = (
            Comment.unscoped.filter(
                content_type=ct,
                object_id=OuterRef("pk"),
            )
            .values("object_id")
            .annotate(c=Count("id"))
            .values("c")[:1]
        )
        qs = qs.annotate(
            comment_count=models.functions.Coalesce(
                Subquery(comment_count_sq), 0
            )
        )

        # Prefetch reactions for efficient serialization
        qs = qs.prefetch_related("reactions")

        # Filter out expired posts
        qs = qs.filter(
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=timezone.now())
        )

        # Non-admin users only see published posts
        if self.action in ("list", "retrieve"):
            membership = getattr(self.request, "_cached_tenant_membership", None)
            if not membership or membership.role.hierarchy_level > 20:
                qs = qs.filter(is_published=True)

        return qs

    def perform_create(self, serializer):
        serializer.save(author=self.request.user)

    @action(detail=True, methods=["post", "delete"], url_path="react")
    def react(self, request, pk=None):
        """Toggle a reaction on a post."""
        post = self.get_object()

        if request.method == "DELETE":
            NewsPostReaction.objects.filter(post=post, user=request.user).delete()
            return Response(status=status.HTTP_204_NO_CONTENT)

        reaction_type = request.data.get("reaction")
        if reaction_type not in ReactionType.values:
            return Response(
                {"detail": "Invalid reaction type."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        existing = NewsPostReaction.objects.filter(post=post, user=request.user).first()
        if existing and existing.reaction == reaction_type:
            # Toggle off: clicking same emoji removes reaction
            existing.delete()
            return Response({"removed": True, "reaction": reaction_type})

        NewsPostReaction.objects.update_or_create(
            post=post,
            user=request.user,
            defaults={"reaction": reaction_type},
        )
        return Response({"removed": False, "reaction": reaction_type})

    @action(detail=True, methods=["post"], url_path="mark-read")
    def mark_read(self, request, pk=None):
        """Mark a single post as read."""
        post = self.get_object()
        NewsPostRead.objects.get_or_create(post=post, user=request.user)
        return Response({"status": "read"})

    @action(detail=False, methods=["post"], url_path="mark-all-read")
    def mark_all_read(self, request):
        """Bulk mark all unread posts as read."""
        unread_posts = (
            NewsPost.objects.exclude(
                reads__user=request.user
            )
            .filter(is_published=True)
            .filter(
                models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=timezone.now())
            )
        )
        reads = [
            NewsPostRead(post=post, user=request.user)
            for post in unread_posts
        ]
        NewsPostRead.objects.bulk_create(reads, ignore_conflicts=True)
        return Response({"status": "all_read", "count": len(reads)})

    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        """Get count of unread published posts."""
        count = (
            NewsPost.objects.filter(is_published=True)
            .filter(
                models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=timezone.now())
            )
            .exclude(reads__user=request.user)
            .count()
        )
        return Response({"count": count})
