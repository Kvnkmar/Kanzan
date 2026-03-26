"""
Tests that PlanLimitChecker is actually called from ViewSet endpoints.

These are integration tests that hit the real API endpoints and verify
that plan limits return 403 when exceeded.
"""

from rest_framework import status
from rest_framework.test import APIClient

from apps.billing.models import UsageTracker

from tests.base import TenantTestCase


class TicketCreatePlanEnforcementTest(TenantTestCase):
    """POST /api/v1/tickets/tickets/ must respect plan limits."""

    def setUp(self):
        super().setUp()
        self.sub, self.usage = self.create_subscription(
            self.tenant_a, self.free_plan,
        )
        self.set_tenant(self.tenant_a)
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin_a)

    def test_ticket_create_blocked_at_limit(self):
        """Creating a ticket when at the monthly limit returns 403."""
        self.usage.tickets_created = self.free_plan.max_tickets_per_month
        self.usage.save()

        response = self.client.post(
            "/api/v1/tickets/tickets/",
            {
                "subject": "Should be blocked",
                "description": "Over limit",
                "priority": "medium",
            },
            format="json",
            HTTP_HOST="tenant-a.localhost",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("upgrade", response.json()["detail"].lower())

    def test_ticket_create_allowed_under_limit(self):
        """Creating a ticket under the limit succeeds."""
        self.usage.tickets_created = 0
        self.usage.save()

        response = self.client.post(
            "/api/v1/tickets/tickets/",
            {
                "subject": "Should work",
                "description": "Under limit",
                "priority": "medium",
            },
            format="json",
            HTTP_HOST="tenant-a.localhost",
        )
        self.assertIn(
            response.status_code,
            [status.HTTP_200_OK, status.HTTP_201_CREATED],
        )


class ContactCreatePlanEnforcementTest(TenantTestCase):
    """POST /api/v1/contacts/contacts/ must respect plan limits."""

    def setUp(self):
        super().setUp()
        self.sub, self.usage = self.create_subscription(
            self.tenant_a, self.free_plan,
        )
        self.set_tenant(self.tenant_a)
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin_a)

    def test_contact_create_blocked_at_limit(self):
        """Creating a contact when at the limit returns 403."""
        self.usage.contacts_count = self.free_plan.max_contacts
        self.usage.save()

        response = self.client.post(
            "/api/v1/contacts/contacts/",
            {
                "first_name": "Test",
                "last_name": "Contact",
                "email": "test@blocked.com",
            },
            format="json",
            HTTP_HOST="tenant-a.localhost",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
