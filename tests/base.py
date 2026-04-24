"""
Shared test infrastructure for Kanzen Suites.

Provides KanzenBaseTestCase which sets up two isolated tenants with users,
roles, and memberships — the minimum scaffolding needed to test
multi-tenancy, RBAC, and plan limits.
"""

from datetime import date

from django.test import TestCase, RequestFactory
from django.utils import timezone as tz
from rest_framework.test import APIClient

from apps.accounts.models import Role, TenantMembership, User
from apps.billing.models import Plan, Subscription, UsageTracker
from apps.contacts.models import Contact
from apps.tenants.models import Tenant
from apps.tickets.models import Queue, SLAPolicy, Ticket, TicketStatus
from main.context import clear_current_tenant, set_current_tenant


class TenantTestCase(TestCase):
    """
    Legacy base test case — kept for backward compatibility.

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
            current_period_start=tz.make_aware(tz.datetime(2026, 1, 1)),
            current_period_end=tz.make_aware(tz.datetime(2026, 12, 31)),
        )
        usage = UsageTracker.objects.create(
            tenant=tenant,
            period_start=date(2026, 1, 1),
        )
        return sub, usage


class KanzenBaseTestCase(TenantTestCase):
    """
    Extended base test case for the comprehensive QA suite.

    Adds on top of TenantTestCase:
        - manager_a user (tenant A)
        - admin_b, agent_b users (tenant B)
        - Full ticket status set for tenant A (open, in-progress, waiting, resolved, closed)
        - SLAPolicy for tenant A (priority=high)
        - Queue for tenant A
        - Contact for tenant A
        - Helper: self.auth(user) → sets JWT-like Authorization via force_authenticate
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        # --- Additional users ---
        cls.manager_a = User.objects.create_user(
            email="manager@tenant-a.test",
            password="testpass123",
            first_name="Manager",
            last_name="A",
        )
        TenantMembership.objects.create(
            user=cls.manager_a, tenant=cls.tenant_a, role=cls.role_manager_a,
        )

        cls.agent_b = User.objects.create_user(
            email="agent@tenant-b.test",
            password="testpass123",
            first_name="Agent",
            last_name="B",
        )
        cls.role_agent_b = Role.unscoped.get(tenant=cls.tenant_b, slug="agent")
        TenantMembership.objects.create(
            user=cls.agent_b, tenant=cls.tenant_b, role=cls.role_agent_b,
        )

        # --- Full status set for tenant A ---
        cls.status_in_progress_a = TicketStatus(
            name="In Progress", slug="in-progress", order=1,
            tenant=cls.tenant_a,
        )
        cls.status_in_progress_a.save()

        cls.status_waiting_a = TicketStatus(
            name="Waiting", slug="waiting", order=2,
            pauses_sla=True,
            tenant=cls.tenant_a,
        )
        cls.status_waiting_a.save()

        cls.status_resolved_a = TicketStatus(
            name="Resolved", slug="resolved", order=3,
            tenant=cls.tenant_a,
        )
        cls.status_resolved_a.save()

        # Update closed status order for consistency
        cls.status_closed_a.order = 4
        cls.status_closed_a.save()

        # --- SLA Policy ---
        cls.sla_policy_a = SLAPolicy(
            name="High Priority SLA",
            priority="high",
            first_response_minutes=5,
            resolution_minutes=30,
            business_hours_only=False,
            is_active=True,
            tenant=cls.tenant_a,
        )
        cls.sla_policy_a.save()

        # --- Queue ---
        cls.queue_a = Queue(
            name="Support Queue",
            auto_assign=False,
            tenant=cls.tenant_a,
        )
        cls.queue_a.save()

        # --- Contact ---
        cls.contact_a = Contact(
            first_name="John",
            last_name="Doe",
            email="john@customer.com",
            tenant=cls.tenant_a,
        )
        cls.contact_a.save()

    def setUp(self):
        super().setUp()
        self.client = APIClient()

    def auth(self, user):
        """Authenticate the API client as the given user."""
        self.client.force_authenticate(user=user)

    def auth_tenant(self, user, tenant=None):
        """Authenticate and set tenant subdomain header."""
        self.client.force_authenticate(user=user)
        t = tenant or self.tenant_a
        self.client.defaults["HTTP_HOST"] = f"{t.slug}.localhost:8001"

    def api_url(self, path):
        """Build a full API URL."""
        if not path.startswith("/"):
            path = f"/{path}"
        return f"/api/v1{path}"

    def create_ticket(self, tenant=None, user=None, **kwargs):
        """Create a ticket via the model layer (not API)."""
        t = tenant or self.tenant_a
        u = user or self.admin_a
        return self.make_ticket(t, u, **kwargs)
