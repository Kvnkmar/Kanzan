"""
Module 17 — Knowledge Base Tests

Tests for:
- Agent can create KB articles
- Viewer cannot create KB articles (403)
- Published articles accessible to all tenant members
- Draft articles filtered from agent view
- Article-category linking
- KB coverage flag on ticket closure
- Cross-tenant article isolation
"""

from django.utils import timezone

from apps.knowledge.models import Article, Category as KBCategory
from apps.tickets.models import Ticket

from tests.base import KanzenBaseTestCase


class TestKBArticleCreation(KanzenBaseTestCase):
    """17.1 - 17.2: Role-based KB article creation permissions."""

    def setUp(self):
        super().setUp()
        self.set_tenant(self.tenant_a)
        self.kb_category = KBCategory(
            name="General",
            tenant=self.tenant_a,
        )
        self.kb_category.save()

    def _article_payload(self):
        return {
            "title": "How to reset your password",
            "content": "Step-by-step guide for password reset.",
            "status": "draft",
            "category": str(self.kb_category.pk),
        }

    def test_17_1_agent_can_create_kb_article(self):
        """Agent (hierarchy_level=30) can create a KB article."""
        self.auth_tenant(self.agent_a, self.tenant_a)
        url = self.api_url("/knowledge/articles/")
        resp = self.client.post(url, self._article_payload(), format="json")
        self.assertIn(resp.status_code, [200, 201],
                      f"Agent should be able to create KB articles. Got {resp.status_code}: {resp.data}")

    def test_17_2_viewer_cannot_create_kb_article(self):
        """Viewer role cannot create KB articles (should get 403)."""
        self.auth_tenant(self.viewer_a, self.tenant_a)
        url = self.api_url("/knowledge/articles/")
        resp = self.client.post(url, self._article_payload(), format="json")
        self.assertEqual(resp.status_code, 403,
                         f"Viewer should not be able to create KB articles. Got {resp.status_code}: {resp.data}")


class TestKBArticleAccess(KanzenBaseTestCase):
    """17.3 - 17.4: Published and draft article visibility."""

    def setUp(self):
        super().setUp()
        self.set_tenant(self.tenant_a)
        self.kb_category = KBCategory(
            name="Support",
            tenant=self.tenant_a,
        )
        self.kb_category.save()

        self.published_article = Article(
            title="Published Guide",
            content="This is a published article.",
            status="published",
            category=self.kb_category,
            author=self.admin_a,
            tenant=self.tenant_a,
            published_at=timezone.now(),
        )
        self.published_article.save()

        self.draft_article = Article(
            title="Draft Internal Notes",
            content="This is a draft article.",
            status="draft",
            category=self.kb_category,
            author=self.admin_a,
            tenant=self.tenant_a,
        )
        self.draft_article.save()

    def test_17_3_published_article_accessible_to_all_members(self):
        """Published article is visible to agents (all tenant members)."""
        self.auth_tenant(self.agent_a, self.tenant_a)
        url = self.api_url(f"/knowledge/articles/{self.published_article.pk}/")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200,
                         f"Published article should be accessible to agents. Got {resp.status_code}")

    def test_17_4_draft_article_not_accessible_to_agent(self):
        """Draft article authored by admin is not visible to agent in list view."""
        self.auth_tenant(self.agent_a, self.tenant_a)
        url = self.api_url("/knowledge/articles/")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        # Agent should NOT see the admin's draft article
        article_ids = [str(a["id"]) for a in resp.data.get("results", resp.data)]
        self.assertNotIn(
            str(self.draft_article.pk),
            article_ids,
            "Agent should not see draft articles authored by other users.",
        )


class TestKBArticleCategoryLink(KanzenBaseTestCase):
    """17.5: KB article linked to a category."""

    def setUp(self):
        super().setUp()
        self.set_tenant(self.tenant_a)
        self.kb_category = KBCategory(
            name="Troubleshooting",
            tenant=self.tenant_a,
        )
        self.kb_category.save()

    def test_17_5_article_linked_to_category(self):
        """An article can be created and linked to a KB category."""
        self.auth_tenant(self.admin_a, self.tenant_a)
        url = self.api_url("/knowledge/articles/")
        data = {
            "title": "Troubleshooting Network Issues",
            "content": "Common network troubleshooting steps.",
            "status": "published",
            "category": str(self.kb_category.pk),
        }
        resp = self.client.post(url, data, format="json")
        self.assertIn(resp.status_code, [200, 201],
                      f"Article creation should succeed. Got {resp.status_code}: {resp.data}")

        # Verify the article is linked to the category
        self.set_tenant(self.tenant_a)
        article = Article.objects.get(title="Troubleshooting Network Issues")
        self.assertEqual(article.category_id, self.kb_category.pk)


class TestKBCoverageOnClosure(KanzenBaseTestCase):
    """17.6: KB coverage flag set on ticket closure when category has <3 published articles."""

    def setUp(self):
        super().setUp()
        self.set_tenant(self.tenant_a)
        self.kb_category = KBCategory(
            name="Networking",
            tenant=self.tenant_a,
        )
        self.kb_category.save()

        # Create only 1 published article for the "Networking" category
        Article(
            title="Network Basics",
            content="Introduction to networking.",
            status="published",
            category=self.kb_category,
            author=self.admin_a,
            tenant=self.tenant_a,
        ).save()

    def test_17_6_closure_sets_needs_kb_article_flag(self):
        """Closing a ticket with category that has <3 published articles sets needs_kb_article."""
        ticket = self.create_ticket(
            tenant=self.tenant_a,
            user=self.admin_a,
            subject="Networking issue",
            category="Networking",
        )

        from apps.tickets.services import change_ticket_status
        self.set_tenant(self.tenant_a)
        # ticket_closed signal fires via transaction.on_commit
        with self.captureOnCommitCallbacks(execute=True):
            change_ticket_status(ticket, self.status_closed_a, actor=self.admin_a)

        ticket.refresh_from_db()
        self.assertTrue(
            ticket.needs_kb_article,
            "Ticket should be flagged for KB article when category has <3 published articles.",
        )


class TestKBTenantIsolation(KanzenBaseTestCase):
    """17.7: Cross-tenant KB article access is prevented."""

    def setUp(self):
        super().setUp()
        self.set_tenant(self.tenant_a)
        self.kb_category_a = KBCategory(
            name="Tenant A Docs",
            tenant=self.tenant_a,
        )
        self.kb_category_a.save()

        self.article_a = Article(
            title="Tenant A Secret Guide",
            content="Confidential content for Tenant A.",
            status="published",
            category=self.kb_category_a,
            author=self.admin_a,
            tenant=self.tenant_a,
        )
        self.article_a.save()

    def test_17_7_tenant_b_cannot_access_tenant_a_articles(self):
        """admin_b authenticated against tenant_b cannot access tenant_a's KB articles."""
        self.auth_tenant(self.admin_b, self.tenant_b)
        url = self.api_url(f"/knowledge/articles/{self.article_a.pk}/")
        resp = self.client.get(url)
        # Should be 404 (tenant-scoped manager filters it out) or 403
        self.assertIn(resp.status_code, [403, 404],
                      f"Tenant B should not access Tenant A articles. Got {resp.status_code}")
