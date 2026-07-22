"""Regression tests for the go-live blocker fixes.

Blocker #1 — a freshly provisioned tenant seeded 0 TicketStatus/Queue/Category,
so the very first ticket could not be created (the create path 400s on
"No default status configured"). ``seed_default_ticket_config`` is now called
from both onboarding paths (setup_company + provision_tenant).

Blocker #4 — a lapsed subscription 402'd the billing API itself, so the tenant
could never reach checkout to re-subscribe. The whole ``/api/v1/billing/``
prefix (and ``/auth/handoff/``) is now exempt from SubscriptionMiddleware.
"""

import pytest
from django.test import RequestFactory

from conftest import SubscriptionFactory


# ---------------------------------------------------------------------------
# Blocker #1 — default ticket config seeding
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSeedDefaultTicketConfig:
    def test_seeds_statuses_queues_categories(self, tenant):
        from apps.tickets.defaults import seed_default_ticket_config
        from apps.tickets.models import Queue, TicketCategory, TicketStatus

        # A fresh tenant (TenantFactory) has none of these.
        assert not TicketStatus.unscoped.filter(tenant=tenant).exists()

        created = seed_default_ticket_config(tenant)

        assert created == {"statuses": 5, "queues": 4, "categories": 4}
        # Exactly one default status — the thing the create path requires.
        assert TicketStatus.unscoped.filter(tenant=tenant, is_default=True).count() == 1
        assert TicketStatus.unscoped.filter(tenant=tenant).count() == 5
        assert Queue.unscoped.filter(tenant=tenant).count() == 4
        assert TicketCategory.unscoped.filter(tenant=tenant).count() == 4

    def test_idempotent(self, tenant):
        from apps.tickets.defaults import seed_default_ticket_config
        from apps.tickets.models import TicketStatus

        seed_default_ticket_config(tenant)
        second = seed_default_ticket_config(tenant)

        assert second == {"statuses": 0, "queues": 0, "categories": 0}
        assert TicketStatus.unscoped.filter(tenant=tenant).count() == 5  # no dupes
        assert TicketStatus.unscoped.filter(tenant=tenant, is_default=True).count() == 1

    def test_seeded_tenant_can_create_first_ticket(self, tenant, admin_user, admin_client):
        """End-to-end proof the blocker is closed: after seeding, the create
        endpoint returns 201 instead of 400 'No default status configured'."""
        from apps.tickets.defaults import seed_default_ticket_config
        from apps.tickets.models import Queue

        seed_default_ticket_config(tenant)
        queue = Queue.unscoped.filter(tenant=tenant, name="Support").first()

        resp = admin_client.post(
            "/api/v1/tickets/tickets/",
            {
                "subject": "First ticket",
                "description": "hello",
                "priority": "medium",
                "queue": str(queue.id),
            },
            format="json",
        )
        assert resp.status_code == 201, getattr(resp, "data", resp)
        assert resp.data["number"] > 0


@pytest.mark.django_db
class TestProvisionTenantSeeds:
    def test_provision_tenant_seeds_ticket_config(self, db, free_plan):
        from django.core.management import call_command

        from apps.tenants.models import Tenant
        from apps.tickets.models import Queue, TicketStatus

        call_command("provision_tenant", name="Acme Co", slug="acme-golive")

        t = Tenant.objects.get(slug="acme-golive")
        assert TicketStatus.unscoped.filter(tenant=t, is_default=True).exists()
        assert Queue.unscoped.filter(tenant=t).count() == 4


# ---------------------------------------------------------------------------
# Blocker #4 — billing recovery path stays reachable while lapsed
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSubscriptionMiddlewareExemptions:
    def _middleware(self):
        from apps.billing.middleware import SubscriptionMiddleware

        sentinel = object()  # what get_response returns when allowed through
        return SubscriptionMiddleware(lambda req: sentinel), sentinel

    def _request(self, tenant, path):
        req = RequestFactory().get(path)
        req.tenant = tenant
        return req

    def _make_lapsed(self, tenant, free_plan):
        # canceled => is_active False AND in_grace_period False => 402 territory
        return SubscriptionFactory(tenant=tenant, plan=free_plan, status="canceled")

    @pytest.mark.parametrize("path", [
        "/api/v1/billing/subscription/",
        "/api/v1/billing/checkout/",
        "/api/v1/billing/invoices/",
        "/auth/handoff/",
    ])
    def test_recovery_paths_reachable_while_lapsed(self, tenant, free_plan, path):
        self._make_lapsed(tenant, free_plan)
        mw, sentinel = self._middleware()
        resp = mw(self._request(tenant, path))
        assert resp is sentinel, f"{path} must bypass the 402 gate so billing can be fixed"

    def test_app_path_still_402s_while_lapsed(self, tenant, free_plan):
        self._make_lapsed(tenant, free_plan)
        mw, _ = self._middleware()
        resp = mw(self._request(tenant, "/api/v1/tickets/tickets/"))
        assert resp.status_code == 402  # enforcement still bites everywhere else

    def test_active_subscription_passes_everything(self, tenant, free_plan):
        SubscriptionFactory(tenant=tenant, plan=free_plan, status="active")
        mw, sentinel = self._middleware()
        resp = mw(self._request(tenant, "/api/v1/tickets/tickets/"))
        assert resp is sentinel
