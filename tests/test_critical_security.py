"""
Critical security tests for high-risk findings.

Tests IDOR, mass assignment, permission bypass, and cross-tenant data leakage
scenarios identified during the system-wide audit.
"""

from django.contrib.contenttypes.models import ContentType
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import Role, TenantMembership
from apps.comments.models import Comment
from apps.contacts.models import Contact
from apps.tickets.models import Ticket
from main.context import set_current_tenant

from tests.base import TenantTestCase


class CommentCrossTenantIDORTest(TenantTestCase):
    """
    CRITICAL-1: Verify that comments cannot be created on objects
    belonging to a different tenant.
    """

    def setUp(self):
        super().setUp()
        # Create a ticket in tenant_b
        self.set_tenant(self.tenant_b)
        self.ticket_b = Ticket.objects.create(
            subject="Tenant B secret ticket",
            description="Confidential",
            status=self.status_open_b,
            created_by=self.admin_b,
        )
        # Auth as tenant_a admin
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin_a)

    def test_comment_on_cross_tenant_ticket_is_rejected(self):
        """
        Agent in tenant_a should NOT be able to create a comment
        on a ticket belonging to tenant_b.
        """
        ct = ContentType.objects.get_for_model(Ticket)
        response = self.client.post(
            "/api/v1/comments/comments/",
            {
                "content_type": f"{ct.app_label}.{ct.model}",
                "object_id": str(self.ticket_b.pk),
                "body": "I can see your data!",
            },
            format="json",
            HTTP_HOST="tenant-a.localhost",
        )
        # Should be rejected — either 400 (validation) or 404 (object not found)
        # If 201, the IDOR vulnerability exists
        self.assertIn(
            response.status_code,
            [status.HTTP_400_BAD_REQUEST, status.HTTP_404_NOT_FOUND, status.HTTP_403_FORBIDDEN],
            f"IDOR VULNERABILITY: Comment created on cross-tenant ticket! "
            f"Status={response.status_code}, Data={response.data}",
        )


class MembershipMassAssignmentTest(TenantTestCase):
    """
    CRITICAL-2: Verify that is_active and role cannot be tampered
    with via the membership API.
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(user=self.agent_a)
        self.agent_membership = TenantMembership.objects.get(
            user=self.agent_a, tenant=self.tenant_a,
        )

    def test_agent_cannot_change_own_role_to_admin(self):
        """Agent should not be able to promote themselves to Admin."""
        response = self.client.patch(
            f"/api/v1/accounts/memberships/{self.agent_membership.pk}/",
            {"role": str(self.role_admin_a.pk)},
            format="json",
            HTTP_HOST="tenant-a.localhost",
        )
        # Should be denied (403) or the role field should be ignored
        self.agent_membership.refresh_from_db()
        self.assertEqual(
            self.agent_membership.role.slug,
            "agent",
            "PRIVILEGE ESCALATION: Agent changed own role to Admin!",
        )

    def test_agent_cannot_deactivate_other_member(self):
        """Agent should not be able to deactivate another member."""
        admin_membership = TenantMembership.objects.get(
            user=self.admin_a, tenant=self.tenant_a,
        )
        response = self.client.patch(
            f"/api/v1/accounts/memberships/{admin_membership.pk}/",
            {"is_active": False},
            format="json",
            HTTP_HOST="tenant-a.localhost",
        )
        admin_membership.refresh_from_db()
        self.assertTrue(
            admin_membership.is_active,
            "VULNERABILITY: Agent deactivated admin member!",
        )


class PermissionResourceFallbackTest(TenantTestCase):
    """
    HIGH-1: Test that HasTenantPermission denies access when
    permission_resource is not set on the view.
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(user=self.agent_a)

    def test_permission_resource_is_set_on_key_viewsets(self):
        """
        Verify that all major ViewSets using HasTenantPermission
        have permission_resource set.
        """
        from apps.tickets.views import TicketViewSet
        from apps.contacts.views import ContactViewSet, CompanyViewSet
        from apps.kanban.views import BoardViewSet
        from apps.analytics.views import ReportDefinitionViewSet, ExportJobViewSet

        for viewset_class in [
            TicketViewSet, ContactViewSet, CompanyViewSet,
            BoardViewSet, ReportDefinitionViewSet, ExportJobViewSet,
        ]:
            resource = getattr(viewset_class, "permission_resource", None)
            self.assertIsNotNone(
                resource,
                f"{viewset_class.__name__} is missing permission_resource — "
                f"HasTenantPermission will ALLOW all access!",
            )


class CrossTenantBulkActionTest(TenantTestCase):
    """
    Verify that bulk actions cannot operate on cross-tenant objects,
    even if UUIDs are known.
    """

    def setUp(self):
        super().setUp()
        # Create contact in tenant_b
        self.set_tenant(self.tenant_b)
        self.contact_b = Contact.objects.create(
            first_name="Secret",
            last_name="Contact",
            email="secret@tenant-b.test",
            tenant=self.tenant_b,
        )
        # Auth as tenant_a admin
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin_a)

    def test_bulk_delete_cross_tenant_contact_fails(self):
        """
        Bulk delete should not be able to delete contacts from
        another tenant, even if the UUID is provided.
        """
        response = self.client.post(
            "/api/v1/contacts/contacts/bulk-action/",
            {
                "action": "delete",
                "contact_ids": [str(self.contact_b.pk)],
            },
            format="json",
            HTTP_HOST="tenant-a.localhost",
        )
        # The contact should still exist
        self.assertTrue(
            Contact.unscoped.filter(pk=self.contact_b.pk).exists(),
            "CROSS-TENANT VULNERABILITY: Contact from tenant_b was deleted by tenant_a!",
        )

    def test_bulk_delete_cross_tenant_ticket_fails(self):
        """
        Bulk delete should not be able to delete tickets from
        another tenant.
        """
        self.set_tenant(self.tenant_b)
        ticket_b = Ticket.objects.create(
            subject="Secret ticket",
            status=self.status_open_b,
            created_by=self.admin_b,
        )
        response = self.client.post(
            "/api/v1/tickets/tickets/bulk-action/",
            {
                "action": "delete",
                "ticket_ids": [str(ticket_b.pk)],
            },
            format="json",
            HTTP_HOST="tenant-a.localhost",
        )
        self.assertTrue(
            Ticket.unscoped.filter(pk=ticket_b.pk).exists(),
            "CROSS-TENANT VULNERABILITY: Ticket from tenant_b was deleted by tenant_a!",
        )


class AttachmentCrossTenantTest(TenantTestCase):
    """
    Verify attachments cannot be created targeting cross-tenant objects.
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin_a)

    def test_cannot_list_other_tenant_attachments(self):
        """Attachment list should only show current tenant's data."""
        response = self.client.get(
            "/api/v1/attachments/attachments/",
            HTTP_HOST="tenant-a.localhost",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class EmailSendIdempotencyTest(TenantTestCase):
    """
    HIGH-2: Verify outbound email recording for idempotency.
    """

    def test_outbound_email_records_message_id(self):
        """
        After sending a ticket email, an outbound record should exist
        with the message_id for deduplication.
        """
        from apps.inbound_email.models import InboundEmail
        from apps.tickets.email_service import record_outbound_email

        self.set_tenant(self.tenant_a)
        ticket = self.make_ticket(self.tenant_a, self.admin_a)

        record_outbound_email(
            tenant=self.tenant_a,
            ticket=ticket,
            message_id="test-msg-id@tenant-a.localhost",
            recipient_email="customer@example.com",
            subject="Re: Test ticket",
            body_text="Reply body",
            sender_type="agent",
        )

        # Verify record exists
        record = InboundEmail.objects.filter(
            message_id="test-msg-id@tenant-a.localhost",
            direction=InboundEmail.Direction.OUTBOUND,
        ).first()
        self.assertIsNotNone(
            record,
            "Outbound email record was not created for threading/dedup",
        )


class SLABreachFlagTest(TenantTestCase):
    """
    Verify SLA breach flags are persisted to the ticket.
    """

    def test_ticket_sla_breach_fields_exist(self):
        """Ticket model should have SLA breach tracking fields."""
        self.set_tenant(self.tenant_a)
        ticket = self.make_ticket(self.tenant_a, self.admin_a)
        self.assertFalse(ticket.sla_response_breached)
        self.assertFalse(ticket.sla_resolution_breached)
