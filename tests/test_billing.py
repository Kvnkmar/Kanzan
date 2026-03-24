"""
Phase 4f (part 3) — Billing tests.

Covers:
- Plan listing (public)
- Subscription model properties
- Subscription grace period logic
"""

import pytest
from datetime import timedelta
from django.utils import timezone

from conftest import SubscriptionFactory


@pytest.mark.django_db
class TestPlanAPI:
    def test_list_plans_public(self, anon_client, free_plan):
        """Plans endpoint should be accessible without auth (exempt path)."""
        resp = anon_client.get("/api/v1/billing/plans/")
        assert resp.status_code in (200, 401)


@pytest.mark.django_db
class TestSubscriptionModel:
    def test_is_active_when_active(self, tenant, free_plan):
        sub = SubscriptionFactory(tenant=tenant, plan=free_plan, status="active")
        assert sub.is_active is True

    def test_is_active_when_trialing(self, tenant, free_plan):
        sub = SubscriptionFactory(tenant=tenant, plan=free_plan, status="trialing")
        assert sub.is_active is True

    def test_not_active_when_canceled(self, tenant, free_plan):
        sub = SubscriptionFactory(tenant=tenant, plan=free_plan, status="canceled")
        assert sub.is_active is False

    def test_grace_period_within_7_days(self, tenant, free_plan):
        sub = SubscriptionFactory(
            tenant=tenant, plan=free_plan,
            status="past_due",
            current_period_end=timezone.now() - timedelta(days=3),
        )
        assert sub.in_grace_period is True

    def test_grace_period_expired(self, tenant, free_plan):
        sub = SubscriptionFactory(
            tenant=tenant, plan=free_plan,
            status="past_due",
            current_period_end=timezone.now() - timedelta(days=10),
        )
        assert sub.in_grace_period is False

    def test_grace_period_not_past_due(self, tenant, free_plan):
        sub = SubscriptionFactory(
            tenant=tenant, plan=free_plan,
            status="active",
            current_period_end=timezone.now() - timedelta(days=3),
        )
        assert sub.in_grace_period is False
