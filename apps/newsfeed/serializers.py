from collections import Counter

from rest_framework import serializers

from apps.newsfeed.models import NewsPost, ReactionType


REACTION_EMOJI_MAP = {
    "thumbs_up": "\U0001f44d",
    "celebration": "\U0001f389",
    "heart": "\u2764\ufe0f",
    "rocket": "\U0001f680",
    "eyes": "\U0001f440",
    "hundred": "\U0001f4af",
}


class NewsPostSerializer(serializers.ModelSerializer):
    author_name = serializers.SerializerMethodField()
    author_initials = serializers.SerializerMethodField()
    reactions = serializers.SerializerMethodField()
    user_reaction = serializers.SerializerMethodField()
    comment_count = serializers.IntegerField(read_only=True, default=0)
    is_read = serializers.BooleanField(read_only=True, default=True)

    class Meta:
        model = NewsPost
        fields = [
            "id",
            "title",
            "content",
            "category",
            "is_pinned",
            "is_published",
            "is_urgent",
            "emoji",
            "expires_at",
            "author",
            "author_name",
            "author_initials",
            "reactions",
            "user_reaction",
            "comment_count",
            "is_read",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "author", "created_at", "updated_at"]

    def get_author_name(self, obj):
        name = obj.author.get_full_name()
        return name if name.strip() else obj.author.email

    def get_author_initials(self, obj):
        name = obj.author.get_full_name()
        if name.strip():
            parts = name.split()
            return "".join(p[0] for p in parts[:2]).upper()
        return obj.author.email[0].upper()

    def get_reactions(self, obj):
        if hasattr(obj, "_prefetched_objects_cache") and "reactions" in obj._prefetched_objects_cache:
            counts = Counter(r.reaction for r in obj.reactions.all())
        else:
            counts = Counter(
                obj.reactions.values_list("reaction", flat=True)
            )
        return {
            rtype: {
                "count": counts.get(rtype, 0),
                "emoji": REACTION_EMOJI_MAP.get(rtype, ""),
            }
            for rtype in ReactionType.values
            if counts.get(rtype, 0) > 0
        }

    def get_user_reaction(self, obj):
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return None
        if hasattr(obj, "_prefetched_objects_cache") and "reactions" in obj._prefetched_objects_cache:
            for r in obj.reactions.all():
                if r.user_id == request.user.id:
                    return r.reaction
            return None
        reaction = obj.reactions.filter(user=request.user).first()
        return reaction.reaction if reaction else None
