"""
Test stubs for Knowledge Base gap-fill features.

These are intentionally minimal stubs — fill in implementation as needed.
"""

import pytest
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import Role, TenantMembership, User
from apps.knowledge.models import Article, KBSearchGap, KBVote
from apps.tenants.models import Tenant
from main.context import clear_current_tenant, set_current_tenant


class KBSearchTests(TestCase):
    """Tests for kb_search() helper."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="KB Test", slug="kb-test")
        cls.user = User.objects.create_user(
            email="kb@test.com", password="testpass123"
        )
        role = Role.unscoped.filter(tenant=cls.tenant, hierarchy_level=30).first()
        TenantMembership.objects.create(
            user=cls.user, tenant=cls.tenant, role=role, is_active=True,
        )

    def setUp(self):
        set_current_tenant(self.tenant)

    def tearDown(self):
        clear_current_tenant()

    @pytest.mark.skipif(
        "sqlite" in str(__import__("django").conf.settings.DATABASES["default"]["ENGINE"]),
        reason="SearchVectorField requires PostgreSQL",
    )
    def test_kb_search_returns_matching_results(self):
        """kb_search() returns results for a matching query."""
        # TODO: Create a published article, trigger search vector update,
        #       then call kb_search() and assert results are returned.
        pass

    @pytest.mark.skipif(
        "sqlite" in str(__import__("django").conf.settings.DATABASES["default"]["ENGINE"]),
        reason="SearchVectorField requires PostgreSQL",
    )
    def test_kb_search_logs_gap_for_zero_results(self):
        """kb_search() creates a KBSearchGap entry when no results are found."""
        # TODO: Call kb_search() with a query that matches nothing,
        #       then assert KBSearchGap.objects.filter(query=...).exists().
        pass


class KBVoteTests(TestCase):
    """Tests for KBVote unique_together constraint."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Vote Test", slug="vote-test")
        cls.user = User.objects.create_user(
            email="voter@test.com", password="testpass123"
        )
        role = Role.unscoped.filter(tenant=cls.tenant, hierarchy_level=30).first()
        TenantMembership.objects.create(
            user=cls.user, tenant=cls.tenant, role=role, is_active=True,
        )

    def setUp(self):
        set_current_tenant(self.tenant)

    def tearDown(self):
        clear_current_tenant()

    def test_vote_unique_together_enforced(self):
        """KBVote(article, session_key) unique_together prevents duplicates."""
        article = Article.objects.create(
            title="Vote Test Article",
            content="Some content",
            status="published",
            author=self.user,
        )
        KBVote.objects.create(
            article=article, helpful=True, session_key="session-1"
        )
        from django.db import IntegrityError

        with self.assertRaises(IntegrityError):
            KBVote.objects.create(
                article=article, helpful=False, session_key="session-1"
            )


class KBSearchViewTests(TestCase):
    """Tests for the KBSearchView API endpoint."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name="Search API Test", slug="search-api-test"
        )
        cls.user = User.objects.create_user(
            email="searchapi@test.com", password="testpass123"
        )
        role = Role.unscoped.filter(tenant=cls.tenant, hierarchy_level=30).first()
        TenantMembership.objects.create(
            user=cls.user, tenant=cls.tenant, role=role, is_active=True,
        )

    def setUp(self):
        set_current_tenant(self.tenant)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def tearDown(self):
        clear_current_tenant()

    def test_search_endpoint_returns_200(self):
        """GET /api/v1/knowledge/search/?q=test returns 200 for authenticated agent."""
        # TODO: Requires tenant middleware simulation or HTTP_HOST header.
        #       Stub — fill in with proper request setup.
        pass
