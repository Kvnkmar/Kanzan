"""Full-text search helpers for the Knowledge Base."""

from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db.models import Q

from apps.knowledge.models import Article

AGENT_VISIBILITY = Q(visibility__in=["internal", "public"])
PORTAL_VISIBILITY = Q(visibility="public")


def kb_search(tenant, query_str, visibility_filter, source="agent"):
    """Return up to 10 published articles ranked by relevance.

    Logs zero-result queries to KBSearchGap for content-gap analysis.
    """
    from apps.knowledge.models import KBSearchGap

    query = SearchQuery(query_str, search_type="websearch")
    results = (
        Article.objects.filter(tenant=tenant, status="published")
        .filter(visibility_filter)
        .filter(search_vector=query)
        .annotate(rank=SearchRank("search_vector", query))
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
