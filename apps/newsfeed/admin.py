from django.contrib import admin

from main.admin import TenantFilteredAdmin

from apps.newsfeed.models import NewsPost, NewsPostReaction, NewsPostRead


@admin.register(NewsPost)
class NewsPostAdmin(TenantFilteredAdmin, admin.ModelAdmin):
    list_display = ["title", "category", "author", "is_pinned", "is_published", "is_urgent", "tenant", "created_at"]
    list_filter = ["category", "is_pinned", "is_published", "is_urgent", "tenant"]
    search_fields = ["title", "content", "author__email"]
    readonly_fields = ["id", "created_at", "updated_at"]
    raw_id_fields = ["author"]


@admin.register(NewsPostReaction)
class NewsPostReactionAdmin(TenantFilteredAdmin, admin.ModelAdmin):
    list_display = ["post", "user", "reaction", "tenant", "created_at"]
    list_filter = ["reaction", "tenant"]
    raw_id_fields = ["post", "user"]
    readonly_fields = ["id", "created_at", "updated_at"]


@admin.register(NewsPostRead)
class NewsPostReadAdmin(admin.ModelAdmin):
    list_display = ["post", "user", "read_at"]
    raw_id_fields = ["post", "user"]
    readonly_fields = ["id", "read_at"]
