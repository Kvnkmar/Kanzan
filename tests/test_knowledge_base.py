"""
Comprehensive tests for the Knowledge Base section.

Covers:
  - Article CRUD (create, read, update, delete)
  - Review workflow: submit for review → approve / reject → resubmit
  - Role-based access: agents create drafts, only managers/admins can approve/reject/edit others
  - All roles can read published articles
  - Category management permissions
  - View counting, file removal, voting
"""

import pytest
from django.utils import timezone

from main.context import clear_current_tenant, set_current_tenant

from conftest import (
    MembershipFactory,
    TenantFactory,
    UserFactory,
    make_api_client,
)

ARTICLES_URL = "/api/v1/knowledge/articles/"
CATEGORIES_URL = "/api/v1/knowledge/categories/"


def _article_url(article_id):
    return f"{ARTICLES_URL}{article_id}/"


def _action_url(article_id, action):
    return f"{ARTICLES_URL}{article_id}/{action}/"


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def category(tenant):
    from apps.knowledge.models import Category

    set_current_tenant(tenant)
    cat = Category.objects.create(tenant=tenant, name="Getting Started", slug="getting-started")
    clear_current_tenant()
    return cat


@pytest.fixture
def agent_user_2(tenant, agent_role):
    """A second agent in the same tenant."""
    user = UserFactory()
    MembershipFactory(user=user, tenant=tenant, role=agent_role)
    return user


@pytest.fixture
def agent_client_2(agent_user_2, tenant):
    return make_api_client(agent_user_2, tenant)


# ── Helper ─────────────────────────────────────────────────────────────


def _create_article(client, title="Test Article", content="Body text", **extra):
    data = {"title": title, "content": content, **extra}
    return client.post(ARTICLES_URL, data, format="json")


# ═══════════════════════════════════════════════════════════════════════
# 1. ARTICLE CREATION
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestArticleCreation:
    """All authenticated members can create articles."""

    def test_admin_creates_article(self, admin_client):
        resp = _create_article(admin_client, title="Admin Guide")
        assert resp.status_code == 201
        assert resp.data["title"] == "Admin Guide"
        assert resp.data["status"] == "draft"

    def test_manager_creates_article(self, manager_client):
        resp = _create_article(manager_client, title="Manager Guide")
        assert resp.status_code == 201

    def test_agent_creates_article(self, agent_client):
        resp = _create_article(agent_client, title="Agent Guide")
        assert resp.status_code == 201
        assert resp.data["status"] == "draft"

    def test_agent_forced_to_draft(self, agent_client):
        """Agents cannot set status to published directly."""
        resp = _create_article(agent_client, title="Sneaky", status="published")
        assert resp.status_code == 201
        assert resp.data["status"] == "draft"

    def test_admin_can_set_status_on_create(self, admin_client):
        """Admins can set status to published directly."""
        resp = _create_article(admin_client, title="Quick Publish", status="published")
        assert resp.status_code == 201
        assert resp.data["status"] == "published"

    def test_article_auto_generates_slug(self, admin_client):
        resp = _create_article(admin_client, title="My Great Article")
        assert resp.status_code == 201
        assert resp.data["slug"] == "my-great-article"

    def test_article_with_category(self, admin_client, category):
        resp = _create_article(admin_client, title="Categorized", category=str(category.id))
        assert resp.status_code == 201

    def test_unauthenticated_cannot_create(self, anon_client):
        resp = _create_article(anon_client, title="Fail")
        assert resp.status_code in (401, 403)


# ═══════════════════════════════════════════════════════════════════════
# 2. ARTICLE LISTING & VISIBILITY
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestArticleVisibility:
    """All roles can see published articles; agents only see own drafts."""

    def _publish_article(self, admin_client, title="Published Article"):
        resp = _create_article(admin_client, title=title, status="published")
        assert resp.status_code == 201
        return resp.data["id"]

    def test_all_roles_see_published(self, admin_client, manager_client, agent_client):
        self._publish_article(admin_client, title="Public KB Article")
        for client in (admin_client, manager_client, agent_client):
            resp = client.get(ARTICLES_URL)
            assert resp.status_code == 200
            titles = [a["title"] for a in resp.data["results"]]
            assert "Public KB Article" in titles

    def test_agent_sees_own_drafts(self, agent_client):
        _create_article(agent_client, title="My Draft")
        resp = agent_client.get(ARTICLES_URL)
        titles = [a["title"] for a in resp.data["results"]]
        assert "My Draft" in titles

    def test_agent_cannot_see_other_agents_drafts(self, agent_client, agent_client_2):
        _create_article(agent_client, title="Agent1 Draft")
        resp = agent_client_2.get(ARTICLES_URL)
        titles = [a["title"] for a in resp.data["results"]]
        assert "Agent1 Draft" not in titles

    def test_admin_sees_all_articles(self, admin_client, agent_client):
        _create_article(agent_client, title="Agent Secret Draft")
        resp = admin_client.get(ARTICLES_URL)
        titles = [a["title"] for a in resp.data["results"]]
        assert "Agent Secret Draft" in titles

    def test_manager_sees_all_articles(self, manager_client, agent_client):
        _create_article(agent_client, title="Agent Hidden Draft")
        resp = manager_client.get(ARTICLES_URL)
        titles = [a["title"] for a in resp.data["results"]]
        assert "Agent Hidden Draft" in titles


# ═══════════════════════════════════════════════════════════════════════
# 3. ARTICLE UPDATE PERMISSIONS
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestArticleUpdate:
    """Only admins/managers can edit any article; agents can only edit their own."""

    def test_admin_can_edit_any_article(self, admin_client, agent_client):
        resp = _create_article(agent_client, title="Agent Work")
        article_id = resp.data["id"]
        resp = admin_client.patch(
            _article_url(article_id), {"title": "Admin Edited"}, format="json"
        )
        assert resp.status_code == 200
        assert resp.data["title"] == "Admin Edited"

    def test_manager_can_edit_any_article(self, manager_client, agent_client):
        resp = _create_article(agent_client, title="Agent Work 2")
        article_id = resp.data["id"]
        resp = manager_client.patch(
            _article_url(article_id), {"title": "Manager Edited"}, format="json"
        )
        assert resp.status_code == 200
        assert resp.data["title"] == "Manager Edited"

    def test_agent_can_edit_own_article(self, agent_client):
        resp = _create_article(agent_client, title="My Article")
        article_id = resp.data["id"]
        resp = agent_client.patch(
            _article_url(article_id), {"title": "My Updated Article"}, format="json"
        )
        assert resp.status_code == 200
        assert resp.data["title"] == "My Updated Article"

    def test_agent_cannot_edit_other_agents_article(self, agent_client, agent_client_2):
        resp = _create_article(agent_client, title="Not Yours")
        article_id = resp.data["id"]
        resp = agent_client_2.patch(
            _article_url(article_id), {"title": "Hacked"}, format="json"
        )
        # 404 because the queryset filters out other agents' drafts entirely
        assert resp.status_code in (403, 404)

    def test_agent_cannot_edit_article_under_review(self, agent_client, admin_client):
        resp = _create_article(agent_client, title="Under Review")
        article_id = resp.data["id"]
        # Submit for review
        agent_client.post(_action_url(article_id, "submit-for-review"))
        # Agent tries to edit while pending review
        resp = agent_client.patch(
            _article_url(article_id), {"title": "Sneaky Edit"}, format="json"
        )
        assert resp.status_code == 403

    def test_agent_cannot_set_status_to_published(self, agent_client):
        resp = _create_article(agent_client, title="Draft Article")
        article_id = resp.data["id"]
        resp = agent_client.patch(
            _article_url(article_id), {"status": "published"}, format="json"
        )
        assert resp.status_code == 200
        # Status should be forced back to draft
        assert resp.data["status"] == "draft"


# ═══════════════════════════════════════════════════════════════════════
# 4. ARTICLE DELETION PERMISSIONS
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestArticleDeletion:
    """Only admins/managers can delete articles."""

    def test_admin_can_delete(self, admin_client):
        resp = _create_article(admin_client, title="To Delete")
        article_id = resp.data["id"]
        resp = admin_client.delete(_article_url(article_id))
        assert resp.status_code == 204

    def test_manager_can_delete(self, manager_client):
        resp = _create_article(manager_client, title="To Delete 2")
        article_id = resp.data["id"]
        resp = manager_client.delete(_article_url(article_id))
        assert resp.status_code == 204

    def test_agent_cannot_delete(self, agent_client):
        resp = _create_article(agent_client, title="Cannot Delete")
        article_id = resp.data["id"]
        resp = agent_client.delete(_article_url(article_id))
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════════════
# 5. REVIEW WORKFLOW — FULL CYCLE
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestReviewWorkflow:
    """Agent creates draft → submits for review → manager approves/rejects."""

    def test_full_approve_cycle(self, agent_client, manager_client, agent_user):
        # 1. Agent creates draft
        resp = _create_article(agent_client, title="New KB Article")
        assert resp.status_code == 201
        article_id = resp.data["id"]
        assert resp.data["status"] == "draft"

        # 2. Agent submits for review
        resp = agent_client.post(_action_url(article_id, "submit-for-review"))
        assert resp.status_code == 200
        assert resp.data["status"] == "pending_review"
        assert resp.data["submitted_at"] is not None

        # 3. Manager approves
        resp = manager_client.post(_action_url(article_id, "approve"))
        assert resp.status_code == 200
        assert resp.data["status"] == "published"
        assert resp.data["reviewer"] is not None
        assert resp.data["reviewed_at"] is not None
        assert resp.data["published_at"] is not None

        # 4. Verify article is now visible to all
        resp = agent_client.get(_article_url(article_id))
        assert resp.status_code == 200
        assert resp.data["status"] == "published"

    def test_full_reject_and_resubmit_cycle(self, agent_client, manager_client):
        # 1. Agent creates draft
        resp = _create_article(agent_client, title="Needs Work")
        article_id = resp.data["id"]

        # 2. Submit for review
        resp = agent_client.post(_action_url(article_id, "submit-for-review"))
        assert resp.status_code == 200

        # 3. Manager rejects with reason
        resp = manager_client.post(
            _action_url(article_id, "reject"),
            {"rejection_reason": "Needs more detail on step 3."},
            format="json",
        )
        assert resp.status_code == 200
        assert resp.data["status"] == "rejected"
        assert resp.data["rejection_reason"] == "Needs more detail on step 3."
        assert resp.data["reviewer"] is not None

        # 4. Agent updates and resubmits
        resp = agent_client.patch(
            _article_url(article_id),
            {"content": "Updated content with more detail on step 3."},
            format="json",
        )
        assert resp.status_code == 200

        resp = agent_client.post(_action_url(article_id, "submit-for-review"))
        assert resp.status_code == 200
        assert resp.data["status"] == "pending_review"
        # Previous rejection data should be cleared
        assert resp.data["rejection_reason"] == ""
        assert resp.data["reviewer"] is None
        assert resp.data["reviewed_at"] is None

    def test_admin_can_also_approve(self, agent_client, admin_client):
        resp = _create_article(agent_client, title="For Admin Review")
        article_id = resp.data["id"]
        agent_client.post(_action_url(article_id, "submit-for-review"))

        resp = admin_client.post(_action_url(article_id, "approve"))
        assert resp.status_code == 200
        assert resp.data["status"] == "published"

    def test_admin_can_also_reject(self, agent_client, admin_client):
        resp = _create_article(agent_client, title="Admin Rejects")
        article_id = resp.data["id"]
        agent_client.post(_action_url(article_id, "submit-for-review"))

        resp = admin_client.post(
            _action_url(article_id, "reject"),
            {"rejection_reason": "Not relevant."},
            format="json",
        )
        assert resp.status_code == 200
        assert resp.data["status"] == "rejected"


# ═══════════════════════════════════════════════════════════════════════
# 6. REVIEW WORKFLOW — PERMISSION CHECKS
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestReviewPermissions:
    """Agents cannot approve/reject; only valid transitions allowed."""

    def test_agent_cannot_approve(self, agent_client, manager_client):
        resp = _create_article(agent_client, title="Agent Approves?")
        article_id = resp.data["id"]
        agent_client.post(_action_url(article_id, "submit-for-review"))

        resp = agent_client.post(_action_url(article_id, "approve"))
        assert resp.status_code == 403

    def test_agent_cannot_reject(self, agent_client, manager_client):
        resp = _create_article(agent_client, title="Agent Rejects?")
        article_id = resp.data["id"]
        agent_client.post(_action_url(article_id, "submit-for-review"))

        resp = agent_client.post(
            _action_url(article_id, "reject"),
            {"rejection_reason": "No good."},
            format="json",
        )
        assert resp.status_code == 403

    def test_cannot_approve_draft(self, manager_client):
        resp = _create_article(manager_client, title="Still Draft")
        article_id = resp.data["id"]
        resp = manager_client.post(_action_url(article_id, "approve"))
        assert resp.status_code == 400

    def test_cannot_approve_already_published(self, admin_client):
        resp = _create_article(admin_client, title="Already Published", status="published")
        article_id = resp.data["id"]
        resp = admin_client.post(_action_url(article_id, "approve"))
        assert resp.status_code == 400

    def test_cannot_reject_draft(self, manager_client):
        resp = _create_article(manager_client, title="Draft Reject")
        article_id = resp.data["id"]
        resp = manager_client.post(
            _action_url(article_id, "reject"),
            {"rejection_reason": "No."},
            format="json",
        )
        assert resp.status_code == 400

    def test_reject_requires_reason(self, agent_client, manager_client):
        resp = _create_article(agent_client, title="No Reason")
        article_id = resp.data["id"]
        agent_client.post(_action_url(article_id, "submit-for-review"))

        # Empty reason
        resp = manager_client.post(
            _action_url(article_id, "reject"), {}, format="json"
        )
        assert resp.status_code == 400

    def test_cannot_submit_published_for_review(self, admin_client):
        resp = _create_article(admin_client, title="Already Live", status="published")
        article_id = resp.data["id"]
        resp = admin_client.post(_action_url(article_id, "submit-for-review"))
        assert resp.status_code == 400

    def test_other_agent_cannot_submit_for_review(self, agent_client, agent_client_2):
        resp = _create_article(agent_client, title="Agent1 Article")
        article_id = resp.data["id"]
        resp = agent_client_2.post(_action_url(article_id, "submit-for-review"))
        # 404 because the queryset filters out other agents' drafts entirely
        assert resp.status_code in (403, 404)

    def test_manager_can_submit_any_article(self, agent_client, manager_client):
        """Managers can submit anyone's article for review."""
        resp = _create_article(agent_client, title="Agent Draft For Manager")
        article_id = resp.data["id"]
        resp = manager_client.post(_action_url(article_id, "submit-for-review"))
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════
# 7. NOTIFICATIONS
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestReviewNotifications:
    """Notifications are sent during the review workflow."""

    def test_submit_notifies_managers(
        self, agent_client, manager_user, admin_user, tenant
    ):
        from apps.notifications.models import Notification

        resp = _create_article(agent_client, title="Notify Test")
        article_id = resp.data["id"]
        agent_client.post(_action_url(article_id, "submit-for-review"))

        # Manager and admin should each get a notification
        mgr_notifs = Notification.unscoped.filter(
            tenant=tenant,
            recipient=manager_user,
            type="kb_review_requested",
        )
        assert mgr_notifs.exists()

        admin_notifs = Notification.unscoped.filter(
            tenant=tenant,
            recipient=admin_user,
            type="kb_review_requested",
        )
        assert admin_notifs.exists()

    def test_approve_notifies_author(
        self, agent_client, manager_client, agent_user, tenant
    ):
        from apps.notifications.models import Notification

        resp = _create_article(agent_client, title="Approved Notify")
        article_id = resp.data["id"]
        agent_client.post(_action_url(article_id, "submit-for-review"))
        manager_client.post(_action_url(article_id, "approve"))

        notifs = Notification.unscoped.filter(
            tenant=tenant,
            recipient=agent_user,
            type="kb_article_reviewed",
        )
        assert notifs.exists()
        assert "approved" in notifs.first().body.lower()

    def test_reject_notifies_author(
        self, agent_client, manager_client, agent_user, tenant
    ):
        from apps.notifications.models import Notification

        resp = _create_article(agent_client, title="Rejected Notify")
        article_id = resp.data["id"]
        agent_client.post(_action_url(article_id, "submit-for-review"))
        manager_client.post(
            _action_url(article_id, "reject"),
            {"rejection_reason": "Incomplete info."},
            format="json",
        )

        notifs = Notification.unscoped.filter(
            tenant=tenant,
            recipient=agent_user,
            type="kb_article_reviewed",
        )
        assert notifs.exists()
        assert "Incomplete info." in notifs.first().body


# ═══════════════════════════════════════════════════════════════════════
# 8. RECORD VIEW & VOTE
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestRecordViewAndVote:
    """View counting and helpfulness voting."""

    def test_record_view_increments(self, admin_client):
        resp = _create_article(admin_client, title="Popular", status="published")
        article_id = resp.data["id"]

        resp = admin_client.post(_action_url(article_id, "record-view"))
        assert resp.status_code == 200
        assert resp.data["view_count"] == 1

        resp = admin_client.post(_action_url(article_id, "record-view"))
        assert resp.data["view_count"] == 2

    def test_vote_on_article(self, admin_client):
        resp = _create_article(admin_client, title="Voteable", status="published")
        article_id = resp.data["id"]

        resp = admin_client.post(
            _action_url(article_id, "vote"), {"helpful": True}, format="json"
        )
        assert resp.status_code == 200
        assert resp.data["status"] == "recorded"

    def test_vote_idempotent(self, admin_client):
        """Voting twice from same session updates rather than creating duplicate."""
        from apps.knowledge.models import KBVote

        resp = _create_article(admin_client, title="Vote Once", status="published")
        article_id = resp.data["id"]

        admin_client.post(
            _action_url(article_id, "vote"), {"helpful": True}, format="json"
        )
        admin_client.post(
            _action_url(article_id, "vote"), {"helpful": False}, format="json"
        )

        votes = KBVote.objects.filter(article_id=article_id)
        assert votes.count() == 1
        assert votes.first().helpful is False


# ═══════════════════════════════════════════════════════════════════════
# 9. CATEGORY MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestCategoryManagement:
    """Category CRUD — agents can view, only admin/manager can create/delete."""

    def test_admin_creates_category(self, admin_client):
        resp = admin_client.post(
            CATEGORIES_URL,
            {"name": "FAQs", "slug": "faqs"},
            format="json",
        )
        assert resp.status_code == 201

    def test_manager_creates_category(self, manager_client):
        resp = manager_client.post(
            CATEGORIES_URL,
            {"name": "Guides", "slug": "guides"},
            format="json",
        )
        assert resp.status_code == 201

    def test_agent_can_view_categories(self, agent_client, category):
        resp = agent_client.get(CATEGORIES_URL)
        assert resp.status_code == 200
        assert len(resp.data["results"]) >= 1

    def test_agent_cannot_delete_category(self, agent_client, category):
        resp = agent_client.delete(f"{CATEGORIES_URL}{category.id}/")
        assert resp.status_code == 403

    def test_admin_can_delete_category(self, admin_client, category):
        resp = admin_client.delete(f"{CATEGORIES_URL}{category.id}/")
        assert resp.status_code == 204


# ═══════════════════════════════════════════════════════════════════════
# 10. TENANT ISOLATION
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestKBTenantIsolation:
    """Articles from one tenant are invisible to another."""

    def test_articles_isolated_between_tenants(self, admin_client, tenant):
        # Create article in tenant A
        resp = _create_article(admin_client, title="Tenant A Article", status="published")
        assert resp.status_code == 201

        # Create a different tenant + user
        tenant_b = TenantFactory(name="Tenant B", slug="tenant-b")
        user_b = UserFactory()
        from apps.accounts.models import Role

        role_b = Role.unscoped.get(tenant=tenant_b, slug="admin")
        MembershipFactory(user=user_b, tenant=tenant_b, role=role_b)
        client_b = make_api_client(user_b, tenant_b)

        resp = client_b.get(ARTICLES_URL)
        assert resp.status_code == 200
        titles = [a["title"] for a in resp.data["results"]]
        assert "Tenant A Article" not in titles


# ═══════════════════════════════════════════════════════════════════════
# 11. ARTICLE DETAIL & RETRIEVE
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestArticleDetail:
    """Article detail endpoint returns full content."""

    def test_retrieve_returns_content(self, admin_client):
        resp = _create_article(
            admin_client, title="Detail Test", content="Full body content here."
        )
        article_id = resp.data["id"]
        resp = admin_client.get(_article_url(article_id))
        assert resp.status_code == 200
        assert resp.data["content"] == "Full body content here."
        assert resp.data["title"] == "Detail Test"

    def test_filter_by_status(self, admin_client):
        _create_article(admin_client, title="Draft One")
        _create_article(admin_client, title="Published One", status="published")

        resp = admin_client.get(ARTICLES_URL, {"status": "published"})
        assert resp.status_code == 200
        for article in resp.data["results"]:
            assert article["status"] == "published"

    def test_filter_by_category(self, admin_client, category):
        _create_article(admin_client, title="In Category", category=str(category.id))
        _create_article(admin_client, title="No Category")

        resp = admin_client.get(ARTICLES_URL, {"category": str(category.id)})
        assert resp.status_code == 200
        titles = [a["title"] for a in resp.data["results"]]
        assert "In Category" in titles
        assert "No Category" not in titles
