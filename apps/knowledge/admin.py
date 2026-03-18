from django.contrib import admin

from main.admin import TenantFilteredAdmin

from apps.knowledge.models import Article, Category


@admin.register(Category)
class CategoryAdmin(TenantFilteredAdmin, admin.ModelAdmin):
    list_display = ["name", "slug", "order", "is_active", "tenant"]
    list_filter = ["is_active", "tenant"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Article)
class ArticleAdmin(TenantFilteredAdmin, admin.ModelAdmin):
    list_display = ["title", "category", "author", "status", "is_pinned", "view_count", "published_at", "tenant"]
    list_filter = ["status", "is_pinned", "category", "tenant"]
    search_fields = ["title", "content"]
    prepopulated_fields = {"slug": ("title",)}
    raw_id_fields = ["author", "category"]
    readonly_fields = ["view_count", "published_at", "created_at", "updated_at"]
