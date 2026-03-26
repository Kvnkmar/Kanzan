"""
Tests for PlanLimitChecker enforcement.

Verifies that resource creation is blocked when plan limits are exceeded,
and allowed when within limits or on unlimited plans.
"""

from django.core.exceptions import PermissionDenied

from apps.billing.models import Plan, Subscription, UsageTracker
from apps.billing.services import PlanLimitChecker

from tests.base import TenantTestCase


class PlanLimitCheckerTest(TenantTestCase):
    """PlanLimitChecker raises PermissionDenied when limits are hit."""

    def setUp(self):
        super().setUp()
        self.sub, self.usage = self.create_subscription(
            self.tenant_a, self.free_plan,
        )

    def test_ticket_creation_allowed_under_limit(self):
        """No exception when ticket count is below the limit."""
        self.usage.tickets_created = 0
        self.usage.save()
        checker = PlanLimitChecker(self.tenant_a)
        checker.check_can_create_ticket()  # Should not raise

    def test_ticket_creation_blocked_at_limit(self):
        """PermissionDenied when ticket count reaches the limit."""
        self.usage.tickets_created = self.free_plan.max_tickets_per_month
        self.usage.save()
        checker = PlanLimitChecker(self.tenant_a)
        with self.assertRaises(PermissionDenied) as ctx:
            checker.check_can_create_ticket()
        self.assertIn("upgrade", str(ctx.exception).lower())

    def test_contact_creation_blocked_at_limit(self):
        """PermissionDenied when contact count reaches the limit."""
        self.usage.contacts_count = self.free_plan.max_contacts
        self.usage.save()
        checker = PlanLimitChecker(self.tenant_a)
        with self.assertRaises(PermissionDenied):
            checker.check_can_create_contact()

    def test_user_limit_checked_via_membership_count(self):
        """User limit checks active membership count, not usage tracker."""
        # tenant_a already has 3 memberships (admin, agent, viewer) from setUp
        checker = PlanLimitChecker(self.tenant_a)
        # Free plan allows max_users=3, currently at 3 -> should block
        with self.assertRaises(PermissionDenied):
            checker.check_can_add_user()

    def test_storage_allowed_under_limit(self):
        """Storage check passes when projected usage is under the limit."""
        self.usage.storage_used_mb = 500
        self.usage.save()
        checker = PlanLimitChecker(self.tenant_a)
        checker.check_storage(100)  # 500 + 100 = 600 < 1024

    def test_storage_blocked_when_exceeds_limit(self):
        """Storage check fails when projected usage exceeds the limit."""
        self.usage.storage_used_mb = 1000
        self.usage.save()
        checker = PlanLimitChecker(self.tenant_a)
        with self.assertRaises(PermissionDenied):
            checker.check_storage(100)  # 1000 + 100 = 1100 > 1024

    def test_unlimited_plan_always_allows(self):
        """Enterprise plan (null limits) never blocks."""
        enterprise = Plan.objects.create(
            tier=Plan.Tier.ENTERPRISE,
            name="Enterprise",
            stripe_product_id="prod_ent_test",
            max_users=None,
            max_contacts=None,
            max_tickets_per_month=None,
            max_storage_mb=None,
            max_custom_fields=None,
        )
        self.sub.plan = enterprise
        self.sub.save()
        self.usage.tickets_created = 999999
        self.usage.contacts_count = 999999
        self.usage.storage_used_mb = 999999
        self.usage.save()

        checker = PlanLimitChecker(self.tenant_a)
        checker.check_can_create_ticket()
        checker.check_can_create_contact()
        checker.check_can_add_user()
        checker.check_storage(99999)

    def test_no_subscription_falls_back_to_free(self):
        """Tenant with no subscription uses Free plan limits."""
        # Delete subscription for tenant B (no subscription exists)
        checker = PlanLimitChecker(self.tenant_b)
        # Should use free plan, and free plan allows 100 tickets
        # tenant_b has no usage tracker, so current = 0
        checker.check_can_create_ticket()  # Should not raise

    def test_custom_field_limit(self):
        """Custom field limit is enforced."""
        self.usage.save()
        checker = PlanLimitChecker(self.tenant_a)
        # Free plan allows 5 custom fields, currently at 0
        checker.check_can_add_custom_field("ticket")  # Should not raise
