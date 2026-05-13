"""Full-text search helpers for the Knowledge Base."""

from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db.models import Q

from apps.knowledge.models import Article

AGENT_VISIBILITY = Q(visibility__in=["internal", "public"])
PORTAL_VISIBILITY = Q(visibility="public")


def kb_search(tenant, query_str, visibility_filter, source="agent", user=None):
    """Return up to 10 published articles ranked by relevance.

    If ``user`` is given (and isn't a superuser), the group-restriction
    gate is applied: articles with a non-empty ``allowed_groups`` only
    appear when the user is a member of at least one of those groups
    (or is the author).

    Logs zero-result queries to KBSearchGap for content-gap analysis.
    """
    from apps.knowledge.models import KBSearchGap

    query = SearchQuery(query_str, search_type="websearch")
    qs = (
        Article.objects.filter(tenant=tenant, status="published")
        .filter(visibility_filter)
        .filter(search_vector=query)
    )
    if user is not None and not getattr(user, "is_superuser", False):
        qs = qs.filter(
            Q(allowed_groups__isnull=True)
            | Q(allowed_groups__members=user)
            | Q(author=user)
        ).distinct()

    results = (
        qs.annotate(rank=SearchRank("search_vector", query))
        .order_by("-rank", "-view_count")[:10]
    )
    if not results.exists():
        gap, _ = KBSearchGap.objects.get_or_create(
            tenant=tenant,
            query=query_str.lower()[:255],
            source=source,
            defaults={"count": 0},
        )
        KBSearchGap.objects.filter(pk=gap.pk).update(count=gap.count + 1)
    return results
