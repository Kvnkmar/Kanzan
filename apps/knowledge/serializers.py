"""Serializers for the Knowledge Base app."""

from rest_framework import serializers

from apps.accounts.models import UserGroup
from apps.knowledge.models import Article, Category


class CategorySerializer(serializers.ModelSerializer):
    article_count = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "icon",
            "order",
            "is_active",
            "article_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_article_count(self, obj):
        return obj.articles.filter(status="published").count()


class ArticleListSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(
        source="category.name", read_only=True, default=None
    )
    category_icon = serializers.CharField(
        source="category.icon", read_only=True, default=""
    )
    author_name = serializers.SerializerMethodField()
    reviewer_name = serializers.SerializerMethodField()
    allowed_groups = serializers.SerializerMethodField()
    allowed_group_names = serializers.SerializerMethodField()

    class Meta:
        model = Article
        fields = [
            "id",
            "title",
            "slug",
            "excerpt",
            "category",
            "category_name",
            "category_icon",
            "author",
            "author_name",
            "status",
            "is_pinned",
            "view_count",
            "tags",
            "file",
            "file_name",
            "allowed_groups",
            "allowed_group_names",
            "published_at",
            "submitted_at",
            "reviewed_at",
            "reviewer",
            "reviewer_name",
            "rejection_reason",
            "created_at",
            "updated_at",
        ]

    def get_author_name(self, obj):
        if obj.author:
            name = f"{obj.author.first_name} {obj.author.last_name}".strip()
            return name or obj.author.email
        return None

    def get_reviewer_name(self, obj):
        if obj.reviewer:
            name = f"{obj.reviewer.first_name} {obj.reviewer.last_name}".strip()
            return name or obj.reviewer.email
        return None

    def get_allowed_groups(self, obj):
        return [str(g.id) for g in obj.allowed_groups.all()]

    def get_allowed_group_names(self, obj):
        return [g.name for g in obj.allowed_groups.all()]


class ArticleDetailSerializer(ArticleListSerializer):
    category_detail = CategorySerializer(source="category", read_only=True)

    class Meta(ArticleListSerializer.Meta):
        fields = ArticleListSerializer.Meta.fields + ["content", "category_detail"]


class ArticleCreateSerializer(serializers.ModelSerializer):
    tags = serializers.ListField(
        child=serializers.CharField(), required=False, default=list
    )
    is_pinned = serializers.BooleanField(required=False, default=False)
    allowed_groups = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=UserGroup.objects.none(),
        required=False,
        help_text=(
            "List of UserGroup IDs allowed to view this article. "
            "Empty list = visible to all tenant members. The current "
            "tenant's groups are the only valid choices."
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        tenant = getattr(request, "tenant", None) if request else None
        if tenant:
            self.fields["allowed_groups"].child_relation.queryset = (
                UserGroup.objects.filter(tenant=tenant)
            )

    class Meta:
        model = Article
        fields = [
            "id",
            "title",
            "slug",
            "content",
            "excerpt",
            "category",
            "status",
            "is_pinned",
            "tags",
            "file",
            "file_name",
            "allowed_groups",
            "author",
            "view_count",
            "published_at",
            "submitted_at",
            "reviewed_at",
            "reviewer",
            "rejection_reason",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "author",
            "view_count",
            "published_at",
            "submitted_at",
            "reviewed_at",
            "reviewer",
            "rejection_reason",
            "created_at",
            "updated_at",
        ]
        extra_kwargs = {
            "slug": {"required": False, "allow_blank": True},
            "content": {"required": False, "allow_blank": True},
        }


class ArticleRejectSerializer(serializers.Serializer):
    rejection_reason = serializers.CharField(required=True, min_length=1, max_length=2000)


class KBArticleSearchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Article
        fields = ["id", "title", "slug", "category", "updated_at"]
