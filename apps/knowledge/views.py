"""ViewSets for the Knowledge Base app."""

import mimetypes
import os

from django.db.models import F, Q
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import TenantMembership
from apps.accounts.permissions import HasTenantPermission, _get_membership
from drf_spectacular.utils import extend_schema

from apps.knowledge.models import Article, Category, KBVote
from apps.knowledge.serializers import (
    ArticleCreateSerializer,
    ArticleDetailSerializer,
    ArticleListSerializer,
    ArticleRejectSerializer,
    CategorySerializer,
    KBArticleSearchSerializer,
)
from apps.notifications.models import NotificationType
from apps.notifications.services import send_notification


class CategoryViewSet(viewsets.ModelViewSet):
    """Full CRUD for knowledge base categories."""

    serializer_class = CategorySerializer
    permission_classes = [IsAuthenticated, HasTenantPermission]
    permission_resource = "kb_category"
    search_fields = ["name"]
    ordering_fields = ["name", "order", "created_at"]
    ordering = ["order", "name"]

    def get_queryset(self):
        qs = Category.objects.all()
        if self.request.query_params.get("active_only") == "true":
            qs = qs.filter(is_active=True)
        return qs


class ArticleViewSet(viewsets.ModelViewSet):
    """Full CRUD for knowledge base articles."""

    permission_classes = [IsAuthenticated, HasTenantPermission]
    permission_resource = "kb_article"
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    search_fields = ["title", "content", "excerpt"]
    ordering_fields = ["title", "view_count", "published_at", "created_at"]
    ordering = ["-is_pinned", "-published_at"]
    filterset_fields = ["status", "category", "is_pinned", "author"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Article.objects.none()
        qs = Article.objects.select_related("category", "author").all()
        # Non-admin/manager users only see published articles (or their own drafts)
        user = self.request.user
        if not user.is_superuser:
            from apps.accounts.permissions import _get_membership

            tenant = getattr(self.request, "tenant", None)
            membership = _get_membership(self.request, tenant) if tenant else None
            if membership is None or membership.role.hierarchy_level > 20:
                qs = qs.filter(Q(status="published") | Q(author=user))

        # Filter by slug if provided
        slug = self.request.query_params.get("slug")
        if slug:
            qs = qs.filter(slug=slug)

        # Filter by tag
        tag = self.request.query_params.get("tag")
        if tag:
            qs = qs.filter(tags__contains=[tag])

        return qs

    def get_serializer_class(self):
        if self.action == "list":
            return ArticleListSerializer
        if self.action in ("create", "update", "partial_update"):
            return ArticleCreateSerializer
        return ArticleDetailSerializer

    def _is_admin_or_manager(self):
        tenant = getattr(self.request, "tenant", None)
        if self.request.user.is_superuser:
            return True
        membership = _get_membership(self.request, tenant) if tenant else None
        return membership is not None and membership.role.hierarchy_level <= 20

    def perform_create(self, serializer):
        extra = {"author": self.request.user}
        file = self.request.FILES.get("file")
        if file and not serializer.validated_data.get("file_name"):
            extra["file_name"] = file.name
        # Agents cannot set status to anything other than draft
        if not self._is_admin_or_manager():
            extra["status"] = Article.Status.DRAFT
        serializer.save(**extra)

    def perform_update(self, serializer):
        article = serializer.instance
        file = self.request.FILES.get("file")
        extra = {}
        if file and not serializer.validated_data.get("file_name"):
            extra["file_name"] = file.name
        # Agent restrictions
        if not self._is_admin_or_manager():
            # Agents can only edit their own articles
            if article.author != self.request.user:
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied("You can only edit your own articles.")
            # Agents cannot edit articles that are pending review
            if article.status == Article.Status.PENDING_REVIEW:
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied("This article is locked while under review.")
            # Agents cannot set status to published or pending_review directly
            requested_status = serializer.validated_data.get("status")
            if requested_status in (Article.Status.PUBLISHED, Article.Status.PENDING_REVIEW):
                extra["status"] = Article.Status.DRAFT
        serializer.save(**extra)

    @action(detail=True, methods=["post"], url_path="submit-for-review")
    def submit_for_review(self, request, pk=None):
        article = self.get_object()
        # Only author or admin/manager can submit
        if article.author != request.user and not self._is_admin_or_manager():
            return Response(
                {"detail": "Only the author can submit for review."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if article.status not in (Article.Status.DRAFT, Article.Status.REJECTED):
            return Response(
                {"detail": "Only draft or rejected articles can be submitted for review."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        article.status = Article.Status.PENDING_REVIEW
        article.submitted_at = timezone.now()
        article.rejection_reason = ""
        article.reviewer = None
        article.reviewed_at = None
        article.save(update_fields=[
            "status", "submitted_at", "rejection_reason",
            "reviewer", "reviewed_at", "updated_at",
        ])
        # Notify all admins/managers in tenant
        tenant = request.tenant
        admin_memberships = TenantMembership.objects.select_related("user").filter(
            tenant=tenant, is_active=True, role__hierarchy_level__lte=20,
        )
        author_name = (
            f"{article.author.first_name} {article.author.last_name}".strip()
            if article.author else "Someone"
        ) or (article.author.email if article.author else "Someone")
        for m in admin_memberships:
            if m.user != request.user:
                send_notification(
                    tenant=tenant,
                    recipient=m.user,
                    notification_type=NotificationType.KB_REVIEW_REQUESTED,
                    title=f"KB article submitted for review",
                    body=f'"{article.title}" was submitted by {author_name}.',
                    data={
                        "article_id": str(article.id),
                        "article_title": article.title,
                        "url": f"/knowledge/{article.slug}/",
                    },
                )
        serializer = ArticleDetailSerializer(article)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="approve")
    def approve(self, request, pk=None):
        if not self._is_admin_or_manager():
            return Response(
                {"detail": "Only admins and managers can approve articles."},
                status=status.HTTP_403_FORBIDDEN,
            )
        article = self.get_object()
        if article.status != Article.Status.PENDING_REVIEW:
            return Response(
                {"detail": "Only articles pending review can be approved."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        article.status = Article.Status.PUBLISHED
        article.reviewer = request.user
        article.reviewed_at = timezone.now()
        article.published_at = timezone.now()
        article.save(update_fields=[
            "status", "reviewer", "reviewed_at", "published_at", "updated_at",
        ])
        # Notify the author
        if article.author and article.author != request.user:
            send_notification(
                tenant=request.tenant,
                recipient=article.author,
                notification_type=NotificationType.KB_ARTICLE_REVIEWED,
                title="Your KB article was approved",
                body=f'"{article.title}" has been approved and published.',
                data={
                    "article_id": str(article.id),
                    "article_title": article.title,
                    "action": "approved",
                },
            )
        serializer = ArticleDetailSerializer(article)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="reject")
    def reject(self, request, pk=None):
        if not self._is_admin_or_manager():
            return Response(
                {"detail": "Only admins and managers can reject articles."},
                status=status.HTTP_403_FORBIDDEN,
            )
        reject_serializer = ArticleRejectSerializer(data=request.data)
        reject_serializer.is_valid(raise_exception=True)
        article = self.get_object()
        if article.status != Article.Status.PENDING_REVIEW:
            return Response(
                {"detail": "Only articles pending review can be rejected."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        rejection_reason = reject_serializer.validated_data["rejection_reason"]
        article.status = Article.Status.REJECTED
        article.rejection_reason = rejection_reason
        article.reviewer = request.user
        article.reviewed_at = timezone.now()
        article.save(update_fields=[
            "status", "rejection_reason", "reviewer", "reviewed_at", "updated_at",
        ])
        # Notify the author via dedicated rejection email
        if article.author and article.author != request.user:
            reviewer = request.user
            reviewer_name = (
                f"{reviewer.first_name} {reviewer.last_name}".strip()
                or reviewer.email
            )
            # Build article URL for the email
            tenant = request.tenant
            scheme = "https" if request.is_secure() else "http"
            article_url = f"{scheme}://{request.get_host()}/knowledge/{article.slug}/"

            send_notification(
                tenant=tenant,
                recipient=article.author,
                notification_type=NotificationType.KB_ARTICLE_REVIEWED,
                title="Your KB article needs changes",
                body=f'"{article.title}" was returned with feedback: {rejection_reason}',
                data={
                    "article_id": str(article.id),
                    "article_title": article.title,
                    "action": "rejected",
                    "reason": rejection_reason,
                    "reviewer_name": reviewer_name,
                    "reviewed_at": article.reviewed_at.strftime("%B %d, %Y at %I:%M %p"),
                    "url": article_url,
                },
            )
        serializer = ArticleDetailSerializer(article)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="record-view")
    def record_view(self, request, pk=None):
        article = self.get_object()
        Article.objects.filter(pk=article.pk).update(view_count=F("view_count") + 1)
        article.refresh_from_db()
        return Response({"view_count": article.view_count}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="remove-file")
    def remove_file(self, request, pk=None):
        article = self.get_object()
        if article.file:
            article.file.delete(save=False)
        article.file_name = ""
        article.save(update_fields=["file", "file_name", "updated_at"])
        return Response({"status": "file removed"}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="preview-file")
    def preview_file(self, request, pk=None):
        """Serve a standalone preview page for the article's file.

        Opens in a new tab with a toolbar (filename + download button)
        and the file content rendered below.
        """
        article = self.get_object()
        if not article.file:
            return Response(
                {"detail": "No file attached"}, status=status.HTTP_404_NOT_FOUND
            )

        file_name = article.file_name or os.path.basename(article.file.name)
        ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
        download_url = article.file.url

        body_content = ""

        if ext == "docx":
            try:
                import mammoth
                from django.utils.html import escape as html_escape

                article.file.open("rb")
                result = mammoth.convert_to_html(article.file)
                article.file.close()
                # Sanitize mammoth output: strip <script> and event handlers
                import re

                sanitized = re.sub(
                    r"<script[\s\S]*?</script>", "", result.value, flags=re.IGNORECASE
                )
                sanitized = re.sub(
                    r"\s+on\w+\s*=\s*[\"'][^\"']*[\"']", "", sanitized, flags=re.IGNORECASE
                )
                body_content = (
                    '<div class="doc-content">' + sanitized + "</div>"
                )
            except Exception:
                body_content = (
                    '<div class="error-msg">Failed to preview this document.</div>'
                )
        elif ext == "pdf":
            from django.utils.html import escape as html_escape

            body_content = (
                f'<iframe src="{html_escape(download_url)}#toolbar=1" class="file-frame"></iframe>'
            )
        elif ext in ("png", "jpg", "jpeg", "gif", "webp", "svg", "bmp"):
            from django.utils.html import escape

            body_content = (
                f'<div class="img-wrap"><img src="{escape(download_url)}"'
                f' alt="{escape(file_name)}"></div>'
            )
        elif ext in ("txt", "csv", "log", "md", "json", "xml", "html", "css", "js"):
            try:
                article.file.open("rb")
                text = article.file.read().decode("utf-8", errors="replace")
                article.file.close()
                from django.utils.html import escape

                body_content = f"<pre class='text-content'>{escape(text)}</pre>"
            except Exception:
                body_content = (
                    '<div class="error-msg">Failed to read this file.</div>'
                )
        else:
            body_content = (
                '<div class="error-msg">'
                "<p>Preview is not available for this file type.</p>"
                "<p>Click the download button above to access the file.</p>"
                "</div>"
            )

        from django.utils.html import escape

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(file_name)} - Preview</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:Inter,system-ui,-apple-system,sans-serif;background:#09090b;color:#e4e4e7;min-height:100vh;display:flex;flex-direction:column}}
.toolbar{{position:sticky;top:0;z-index:10;display:flex;align-items:center;justify-content:space-between;padding:0.75rem 1.5rem;background:#18181b;border-bottom:1px solid #27272a;flex-shrink:0}}
.toolbar-left{{display:flex;align-items:center;gap:0.5rem;font-size:0.875rem;font-weight:500;color:#a1a1aa;min-width:0}}
.toolbar-left svg{{flex-shrink:0;width:18px;height:18px;color:#71717a}}
.toolbar-left span{{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.btn-download{{display:inline-flex;align-items:center;gap:0.375rem;padding:0.5rem 1rem;background:#2563eb;color:#fff;border:none;border-radius:0.375rem;font-size:0.8125rem;font-weight:500;cursor:pointer;text-decoration:none;white-space:nowrap}}
.btn-download:hover{{background:#1d4ed8}}
.btn-download svg{{width:16px;height:16px}}
.preview-body{{flex:1;overflow:auto}}
.doc-content{{max-width:900px;margin:0 auto;padding:2rem;line-height:1.75;font-size:0.9375rem}}
.doc-content h1,.doc-content h2,.doc-content h3,.doc-content h4,.doc-content h5,.doc-content h6{{color:#f4f4f5;margin:1.5rem 0 0.5rem}}
.doc-content p{{margin-bottom:0.75rem}}
.doc-content table{{width:100%;border-collapse:collapse;margin:1rem 0}}
.doc-content td,.doc-content th{{border:1px solid #27272a;padding:0.5rem 0.75rem;font-size:0.8125rem}}
.doc-content th{{background:#18181b}}
.doc-content ul,.doc-content ol{{padding-left:1.5rem;margin-bottom:0.75rem}}
.doc-content img{{max-width:100%;height:auto}}
.doc-content a{{color:#3b82f6}}
.file-frame{{width:100%;height:100%;border:none;flex:1;display:block;min-height:calc(100vh - 52px)}}
.img-wrap{{display:flex;align-items:center;justify-content:center;padding:2rem;min-height:calc(100vh - 52px)}}
.img-wrap img{{max-width:100%;max-height:calc(100vh - 100px);object-fit:contain;border-radius:0.5rem}}
.text-content{{max-width:960px;margin:0 auto;padding:1.5rem 2rem;font-size:0.8125rem;line-height:1.6;white-space:pre-wrap;word-wrap:break-word;color:#d4d4d8;font-family:'JetBrains Mono',Consolas,monospace}}
.error-msg{{display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:calc(100vh - 52px);color:#71717a;font-size:0.9375rem;text-align:center;gap:0.5rem}}
</style>
</head>
<body>
<div class="toolbar">
  <div class="toolbar-left">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
    <span>{escape(file_name)}</span>
  </div>
  <a href="{download_url}" download="{escape(file_name)}" class="btn-download">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
    Download
  </a>
</div>
<div class="preview-body">{body_content}</div>
</body>
</html>"""
        return HttpResponse(html, content_type="text/html; charset=utf-8")

    @extend_schema(tags=["Knowledge Base"])
    @action(detail=True, methods=["post"], url_path="vote")
    def vote(self, request, pk=None):
        """Record a helpfulness vote on an article."""
        article = self.get_object()
        helpful = bool(request.data.get("helpful", True))
        session_key = request.session.session_key or request.META.get(
            "REMOTE_ADDR", "anon"
        )
        KBVote.objects.update_or_create(
            article=article,
            session_key=session_key,
            defaults={"helpful": helpful},
        )
        return Response({"status": "recorded"})


@extend_schema(tags=["Knowledge Base"])
class KBSearchView(APIView):
    """Full-text search across published knowledge base articles."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from apps.knowledge.search import AGENT_VISIBILITY, kb_search

        q = request.query_params.get("q", "").strip()
        if len(q) < 2:
            return Response([])
        source = request.query_params.get("src", "agent")
        results = kb_search(
            tenant=request.tenant,
            query_str=q,
            visibility_filter=AGENT_VISIBILITY,
            source=source,
        )
        return Response(KBArticleSearchSerializer(results, many=True).data)
