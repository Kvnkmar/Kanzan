"""
Shared test infrastructure for Kanzan Suite.

Provides TenantTestCase which sets up two isolated tenants with users,
roles, and memberships — the minimum scaffolding needed to test
multi-tenancy, RBAC, and plan limits.
"""

from datetime import date

from django.test import TestCase, RequestFactory

from apps.accounts.models import Role, TenantMembership, User
from apps.billing.models import Plan, Subscription, UsageTracker
from apps.tenants.models import Tenant
from apps.tickets.models import Ticket, TicketStatus
from main.context import clear_current_tenant, set_current_tenant


class TenantTestCase(TestCase):
    """
    Base test case that creates two tenants with full role/user setup.

    Provides:
        self.tenant_a, self.tenant_b  — two isolated tenants
        self.admin_a, self.agent_a    — users in tenant_a
        self.admin_b                  — user in tenant_b
        self.role_admin_a, self.role_agent_a — roles for tenant_a
        self.status_open_a, self.status_closed_a — ticket statuses for tenant_a
        self.status_open_b            — ticket status for tenant_b
        self.free_plan                — Free tier plan
    """

    @classmethod
    def setUpTestData(cls):
        # ------ Tenants ------
        cls.tenant_a = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        cls.tenant_b = Tenant.objects.create(name="Tenant B", slug="tenant-b")

        # ------ Plans ------
        cls.free_plan = Plan.objects.create(
            tier=Plan.Tier.FREE,
            name="Free",
            stripe_product_id="prod_free_test",
            max_users=3,
            max_contacts=500,
            max_tickets_per_month=100,
            max_storage_mb=1024,
            max_custom_fields=5,
        )
        cls.pro_plan = Plan.objects.create(
            tier=Plan.Tier.PRO,
            name="Pro",
            stripe_product_id="prod_pro_test",
            max_users=25,
            max_contacts=10000,
            max_tickets_per_month=5000,
            max_storage_mb=25600,
            max_custom_fields=50,
        )

        # ------ Roles (auto-created by Tenant post_save signal) ------
        cls.role_admin_a = Role.unscoped.get(tenant=cls.tenant_a, slug="admin")
        cls.role_manager_a = Role.unscoped.get(tenant=cls.tenant_a, slug="manager")
        cls.role_agent_a = Role.unscoped.get(tenant=cls.tenant_a, slug="agent")
        cls.role_viewer_a = Role.unscoped.get(tenant=cls.tenant_a, slug="viewer")
        cls.role_admin_b = Role.unscoped.get(tenant=cls.tenant_b, slug="admin")

        # ------ Users ------
        cls.admin_a = User.objects.create_user(
            email="admin@tenant-a.test",
            password="testpass123",
            first_name="Admin",
            last_name="A",
        )
        cls.agent_a = User.objects.create_user(
            email="agent@tenant-a.test",
            password="testpass123",
            first_name="Agent",
            last_name="A",
        )
        cls.viewer_a = User.objects.create_user(
            email="viewer@tenant-a.test",
            password="testpass123",
            first_name="Viewer",
            last_name="A",
        )
        cls.admin_b = User.objects.create_user(
            email="admin@tenant-b.test",
            password="testpass123",
            first_name="Admin",
            last_name="B",
        )

        # ------ Memberships ------
        TenantMembership.objects.create(
            user=cls.admin_a, tenant=cls.tenant_a, role=cls.role_admin_a,
        )
        TenantMembership.objects.create(
            user=cls.agent_a, tenant=cls.tenant_a, role=cls.role_agent_a,
        )
        TenantMembership.objects.create(
            user=cls.viewer_a, tenant=cls.tenant_a, role=cls.role_viewer_a,
        )
        TenantMembership.objects.create(
            user=cls.admin_b, tenant=cls.tenant_b, role=cls.role_admin_b,
        )

        # ------ Ticket Statuses ------
        cls.status_open_a = TicketStatus(
            name="Open", slug="open", order=0, is_default=True,
            tenant=cls.tenant_a,
        )
        cls.status_open_a.save()
        cls.status_closed_a = TicketStatus(
            name="Closed", slug="closed", order=1, is_closed=True,
            tenant=cls.tenant_a,
        )
        cls.status_closed_a.save()

        cls.status_open_b = TicketStatus(
            name="Open", slug="open", order=0, is_default=True,
            tenant=cls.tenant_b,
        )
        cls.status_open_b.save()

    def setUp(self):
        clear_current_tenant()

    def tearDown(self):
        clear_current_tenant()

    def set_tenant(self, tenant):
        """Set the active tenant context for the current test."""
        set_current_tenant(tenant)

    def make_ticket(self, tenant, user, **kwargs):
        """Create a ticket in the given tenant context."""
        self.set_tenant(tenant)
        status = kwargs.pop("status", None)
        if status is None:
            status = TicketStatus.unscoped.filter(
                tenant=tenant, is_default=True,
            ).first()
        return Ticket.objects.create(
            subject=kwargs.pop("subject", "Test ticket"),
            description=kwargs.pop("description", "Test description"),
            status=status,
            created_by=user,
            **kwargs,
        )

    def make_request(self, user, tenant):
        """Create a fake request with user and tenant set."""
        factory = RequestFactory()
        request = factory.get("/")
        request.user = user
        request.tenant = tenant
        return request

    def create_subscription(self, tenant, plan):
        """Create a subscription + usage tracker for a tenant."""
        sub = Subscription.objects.create(
            tenant=tenant,
            plan=plan,
            status=Subscription.Status.ACTIVE,
            stripe_subscription_id=f"sub_test_{tenant.slug}",
            current_period_start="2026-01-01",
            current_period_end="2026-12-31",
        )
        usage = UsageTracker.objects.create(
            tenant=tenant,
            period_start=date(2026, 1, 1),
        )
        return sub, usage
