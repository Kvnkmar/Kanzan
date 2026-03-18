"""ViewSets for the Knowledge Base app."""

import mimetypes
import os

from django.db.models import F, Q
from django.http import HttpResponse
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.permissions import HasTenantPermission
from apps.knowledge.models import Article, Category
from apps.knowledge.serializers import (
    ArticleCreateSerializer,
    ArticleDetailSerializer,
    ArticleListSerializer,
    CategorySerializer,
)


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
    filterset_fields = ["status", "category", "is_pinned"]

    def get_queryset(self):
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

    def perform_create(self, serializer):
        extra = {"author": self.request.user}
        file = self.request.FILES.get("file")
        if file and not serializer.validated_data.get("file_name"):
            extra["file_name"] = file.name
        serializer.save(**extra)

    def perform_update(self, serializer):
        file = self.request.FILES.get("file")
        extra = {}
        if file and not serializer.validated_data.get("file_name"):
            extra["file_name"] = file.name
        serializer.save(**extra)

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

                article.file.open("rb")
                result = mammoth.convert_to_html(article.file)
                article.file.close()
                body_content = (
                    '<div class="doc-content">' + result.value + "</div>"
                )
            except Exception:
                body_content = (
                    '<div class="error-msg">Failed to preview this document.</div>'
                )
        elif ext == "pdf":
            body_content = (
                f'<iframe src="{download_url}#toolbar=1" class="file-frame"></iframe>'
            )
        elif ext in ("png", "jpg", "jpeg", "gif", "webp", "svg", "bmp"):
            from django.utils.html import escape

            body_content = (
                f'<div class="img-wrap"><img src="{download_url}"'
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
