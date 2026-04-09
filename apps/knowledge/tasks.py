"""Celery tasks for the Knowledge Base app."""

from celery import shared_task
from datetime import date

from django.conf import settings
from django.core.mail import send_mail


@shared_task(name="knowledge_base.alert_stale_articles")
def alert_stale_articles():
    """Email authors of published articles past their review_at date.

    Articles overdue by 7+ days are auto-flagged.
    """
    from apps.knowledge.models import Article

    today = date.today()
    stale = (
        Article.unscoped.filter(status="published", review_at__lte=today)
        .select_related("author", "tenant")
    )
    for article in stale:
        if article.author and article.author.email:
            send_mail(
                subject=f"[Kanzen] Review needed: {article.title}",
                message=(
                    f'The article "{article.title}" on '
                    f"{article.tenant.name} is past its review date "
                    f"({article.review_at}). Please update or archive it."
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[article.author.email],
            )
        if (today - article.review_at).days >= 7:
            article.status = "flagged"
            article.save(update_fields=["status", "updated_at"])


@shared_task(name="knowledge_base.send_gap_digest")
def send_gap_digest():
    """Weekly digest of top unanswered KB searches per tenant."""
    from apps.knowledge.models import KBSearchGap
    from apps.tenants.models import Tenant
    from apps.accounts.models import TenantMembership

    for tenant in Tenant.objects.filter(is_active=True):
        gaps = list(
            KBSearchGap.objects.filter(tenant=tenant)
            .order_by("-count")[:10]
        )
        if not gaps:
            continue
        admins = TenantMembership.objects.filter(
            tenant=tenant, role__hierarchy_level__lte=10,
        ).select_related("user")
        body = "Top unanswered KB searches this week:\n\n"
        for g in gaps:
            body += f'  "{g.query}" ({g.source}) -- {g.count} searches\n'
        for m in admins:
            if m.user.email:
                send_mail(
                    subject=f"[Kanzen] KB gap digest -- {tenant.name}",
                    message=body,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[m.user.email],
                )
