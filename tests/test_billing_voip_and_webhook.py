"""Regression tests for billing-1 (VoIP plan seeding) and billing-2
(re-subscribe via Stripe webhook must not violate the OneToOne tenant
constraint)."""

from decimal import Decimal

import pytest
from django.core.management import call_command

from apps.billing.models import Plan, Subscription
from apps.billing.webhooks import _sync_subscription_from_stripe


@pytest.mark.django_db
class TestSeedPlansVoIP:
    def test_paid_tiers_enable_voip(self):
        call_command("seed_plans")
        assert Plan.objects.get(tier="enterprise").has_voip is True
        assert Plan.objects.get(tier="enterprise").has_call_recording is True
        assert Plan.objects.get(tier="pro").has_voip is True
        assert Plan.objects.get(tier="free").has_voip is False


@pytest.mark.django_db
class TestResubscribeWebhook:
    def _stripe_sub(self, sub_id, tenant, price_id):
        return {
            "id": sub_id,
            "customer": "cus_123",
            "status": "active",
            "metadata": {"tenant_id": str(tenant.id)},
            "items": {
                "data": [
                    {"price": {"id": price_id, "recurring": {"interval": "month"}}}
                ]
            },
            "current_period_start": 1700000000,
            "current_period_end": 1702592000,
            "cancel_at_period_end": False,
        }

    def test_resubscribe_repoints_row_without_integrityerror(self, tenant):
        Plan.objects.create(
            tier="pro",
            name="Pro",
            stripe_price_monthly="price_x",
            price_monthly=Decimal("29.00"),
            price_yearly=Decimal("290.00"),
        )

        sub1 = _sync_subscription_from_stripe(
            self._stripe_sub("sub_aaa", tenant, "price_x")
        )
        assert sub1 is not None
        assert Subscription.objects.filter(tenant=tenant).count() == 1

        # Re-subscribe: a brand-new Stripe subscription id for the same tenant.
        # Before the fix this raised IntegrityError on the OneToOne tenant FK.
        sub2 = _sync_subscription_from_stripe(
            self._stripe_sub("sub_bbb", tenant, "price_x")
        )
        assert sub2 is not None
        assert Subscription.objects.filter(tenant=tenant).count() == 1
        assert (
            Subscription.objects.get(tenant=tenant).stripe_subscription_id == "sub_bbb"
        )
