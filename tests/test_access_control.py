"""
Module 12 — Access Control

Tests verifying role-based access control, row-level ticket filtering,
cross-tenant isolation, and permission boundaries for Admin, Manager,
Agent, and Viewer roles.
"""

import unittest

from apps.accounts.models import Role, TenantMembership
from apps.tickets.models import Ticket
from tests.base import KanzenBaseTestCase


class TestAccessControl(KanzenBaseTestCase):
    """Access control and RBAC tests."""

    def setUp(self):
        super().setUp()

    # ------------------------------------------------------------------
    # Helper to create a second agent in tenant_a
    # ------------------------------------------------------------------
    def _create_agent2_a(self):
        """Create a second agent user in tenant_a for isolation tests."""
        from apps.accounts.models import User

        agent2 = User.objects.create_user(
            email="agent2@tenant-a.test",
            password="testpass123",
            first_name="Agent2",
            last_name="A",
        )
        TenantMembership.objects.create(
            user=agent2,
            tenant=self.tenant_a,
            role=self.role_agent_a,
        )
        return agent2

    # ------------------------------------------------------------------
    # 12.1 Admin sees all tenant tickets
    # ------------------------------------------------------------------
    def test_admin_sees_all_tickets(self):
        """Admin can list all tickets in the tenant regardless of creator."""
        # Create tickets by different users
        t1 = self.create_ticket(self.tenant_a, self.admin_a, subject="By Admin")
        t2 = self.create_ticket(self.tenant_a, self.agent_a, subject="By Agent")
        t3 = self.create_ticket(self.tenant_a, self.manager_a, subject="By Manager")

        self.auth_tenant(self.admin_a, self.tenant_a)
        url = self.api_url("/tickets/tickets/")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200, resp.content)

        results = resp.data.get("results", resp.data)
        result_ids = {str(r["id"]) for r in results}
        self.assertIn(str(t1.pk), result_ids)
        self.assertIn(str(t2.pk), result_ids)
        self.assertIn(str(t3.pk), result_ids)

    # ------------------------------------------------------------------
    # 12.2 Manager sees all tenant tickets
    # ------------------------------------------------------------------
    def test_manager_sees_all_tickets(self):
        """Manager can list all tickets in the tenant."""
        t1 = self.create_ticket(self.tenant_a, self.admin_a, subject="By Admin")
        t2 = self.create_ticket(self.tenant_a, self.agent_a, subject="By Agent")

        self.auth_tenant(self.manager_a, self.tenant_a)
        url = self.api_url("/tickets/tickets/")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200, resp.content)

        results = resp.data.get("results", resp.data)
        result_ids = {str(r["id"]) for r in results}
        self.assertIn(str(t1.pk), result_ids)
        self.assertIn(str(t2.pk), result_ids)

    # ------------------------------------------------------------------
    # 12.3 Agent sees only own + assigned tickets
    # ------------------------------------------------------------------
    def test_agent_sees_only_own_and_assigned_tickets(self):
        """
        Agent should see tickets they created or are assigned to,
        but NOT tickets created by others and assigned to someone else.
        """
        # Ticket created by admin, not assigned to agent_a
        t_admin = self.create_ticket(
            self.tenant_a, self.admin_a, subject="Admin's ticket"
        )
        # Ticket created by agent_a
        t_own = self.create_ticket(
            self.tenant_a, self.agent_a, subject="Agent's own ticket"
        )
        # Ticket created by admin but assigned to agent_a
        t_assigned = self.create_ticket(
            self.tenant_a, self.admin_a,
            subject="Assigned to agent", assignee=self.agent_a,
        )

        self.auth_tenant(self.agent_a, self.tenant_a)
        url = self.api_url("/tickets/tickets/")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200, resp.content)

        results = resp.data.get("results", resp.data)
        result_ids = {str(r["id"]) for r in results}

        self.assertIn(str(t_own.pk), result_ids, "Agent should see own tickets")
        self.assertIn(str(t_assigned.pk), result_ids, "Agent should see assigned tickets")
        self.assertNotIn(str(t_admin.pk), result_ids, "Agent should NOT see other's tickets")

    # ------------------------------------------------------------------
    # 12.4 Viewer sees only own + assigned tickets (read-only)
    # ------------------------------------------------------------------
    def test_viewer_sees_only_own_and_assigned_tickets(self):
        """
        Viewer should see only tickets they created or are assigned to.
        """
        # Ticket by admin — viewer should NOT see
        t_admin = self.create_ticket(
            self.tenant_a, self.admin_a, subject="Admin ticket"
        )
        # Ticket by viewer
        t_own = self.create_ticket(
            self.tenant_a, self.viewer_a, subject="Viewer's ticket"
        )
        # Ticket assigned to viewer
        t_assigned = self.create_ticket(
            self.tenant_a, self.admin_a,
            subject="Assigned to viewer", assignee=self.viewer_a,
        )

        self.auth_tenant(self.viewer_a, self.tenant_a)
        url = self.api_url("/tickets/tickets/")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200, resp.content)

        results = resp.data.get("results", resp.data)
        result_ids = {str(r["id"]) for r in results}

        self.assertIn(str(t_own.pk), result_ids, "Viewer should see own tickets")
        self.assertIn(str(t_assigned.pk), result_ids, "Viewer should see assigned tickets")
        self.assertNotIn(str(t_admin.pk), result_ids, "Viewer should NOT see other's tickets")

    # ------------------------------------------------------------------
    # 12.5 Agent cannot access ticket assigned to another agent (IsTicketAccessible)
    # ------------------------------------------------------------------
    def test_agent_cannot_access_others_ticket_detail(self):
        """
        Agent trying to GET a ticket owned by/assigned to another agent
        should receive 403 (IsTicketAccessible blocks direct URL access).
        """
        agent2 = self._create_agent2_a()

        # Ticket created by manager, assigned to agent2
        ticket = self.create_ticket(
            self.tenant_a, self.manager_a,
            subject="Agent2's ticket", assignee=agent2,
        )

        # agent_a tries to access it directly
        self.auth_tenant(self.agent_a, self.tenant_a)
        url = self.api_url(f"/tickets/tickets/{ticket.pk}/")
        resp = self.client.get(url)
        self.assertIn(
            resp.status_code, [403, 404],
            f"Agent should not access another agent's ticket, got {resp.status_code}"
        )

    # ------------------------------------------------------------------
    # 12.6 Cross-tenant ticket URL access blocked
    # ------------------------------------------------------------------
    def test_cross_tenant_ticket_access_blocked(self):
        """
        Admin of tenant_b should not be able to access a ticket
        belonging to tenant_a, even with a direct URL.
        """
        ticket_a = self.create_ticket(
            self.tenant_a, self.admin_a, subject="Tenant A ticket"
        )

        # Authenticate as admin_b with tenant_b subdomain
        self.auth_tenant(self.admin_b, self.tenant_b)
        url = self.api_url(f"/tickets/tickets/{ticket_a.pk}/")
        resp = self.client.get(url)
        self.assertIn(
            resp.status_code, [403, 404],
            f"Cross-tenant access should be blocked, got {resp.status_code}"
        )

    # ------------------------------------------------------------------
    # 12.7 Agent cannot promote their own role
    # ------------------------------------------------------------------
    def test_agent_cannot_change_own_role(self):
        """
        Agent trying to PATCH their own membership to a higher role
        should be denied (403).
        """
        self.auth_tenant(self.agent_a, self.tenant_a)

        # Find agent_a's membership
        membership = TenantMembership.objects.get(
            user=self.agent_a, tenant=self.tenant_a,
        )

        url = self.api_url(f"/accounts/memberships/{membership.pk}/")
        resp = self.client.patch(
            url,
            {"role": str(self.role_admin_a.pk)},
            format="json",
        )
        self.assertIn(
            resp.status_code, [403, 405],
            f"Agent should not be able to change own role, got {resp.status_code}"
        )

    # ------------------------------------------------------------------
    # 12.8 Manager can invite agents but not admins
    # ------------------------------------------------------------------
    def test_manager_cannot_invite_admin_role(self):
        """
        Manager creating an invitation with the Admin role should be
        blocked because the invitee role has a higher hierarchy than the
        inviter's.
        """
        self.auth_tenant(self.manager_a, self.tenant_a)
        self.set_tenant(self.tenant_a)
        # Ensure subscription exists with high user limit so PlanLimitChecker doesn't block
        self.create_subscription(self.tenant_a, self.pro_plan)

        url = self.api_url("/accounts/invitations/")

        # Try inviting with admin role (hierarchy_level=10 < manager's 20)
        resp = self.client.post(url, {
            "email": "newadmin@example.com",
            "role": str(self.role_admin_a.pk),
        }, format="json")
        self.assertIn(
            resp.status_code, [400, 403],
            f"Manager should not invite admin-role users, got {resp.status_code}"
        )

        # Inviting with agent role should succeed (hierarchy_level=30 >= manager's 20)
        resp_agent = self.client.post(url, {
            "email": "newagent@example.com",
            "role": str(self.role_agent_a.pk),
        }, format="json")
        self.assertIn(
            resp_agent.status_code, [200, 201],
            f"Manager should be able to invite agents, got {resp_agent.status_code}"
        )

    # ------------------------------------------------------------------
    # 12.9 Admin can access all CRUD operations
    # ------------------------------------------------------------------
    def test_admin_full_crud(self):
        """Admin can create, read, update, and delete tickets."""
        self.auth_tenant(self.admin_a, self.tenant_a)
        tickets_url = self.api_url("/tickets/tickets/")

        # CREATE
        resp_create = self.client.post(tickets_url, {
            "subject": "Admin CRUD test",
            "description": "Testing full CRUD",
            "priority": "medium",
            "status": str(self.status_open_a.pk),
        }, format="json")
        self.assertIn(resp_create.status_code, [200, 201], resp_create.content)
        ticket_id = resp_create.data["id"]

        detail_url = self.api_url(f"/tickets/tickets/{ticket_id}/")

        # READ
        resp_read = self.client.get(detail_url)
        self.assertEqual(resp_read.status_code, 200, resp_read.content)

        # UPDATE
        resp_update = self.client.patch(
            detail_url, {"subject": "Updated by admin"}, format="json"
        )
        self.assertEqual(resp_update.status_code, 200, resp_update.content)

        # DELETE
        resp_delete = self.client.delete(detail_url)
        self.assertIn(
            resp_delete.status_code, [204, 200],
            f"Admin should be able to delete tickets, got {resp_delete.status_code}"
        )

    # ------------------------------------------------------------------
    # 12.10 Viewer cannot POST/PATCH/DELETE any resource
    # ------------------------------------------------------------------
    def test_viewer_cannot_create_update_delete(self):
        """Viewer (hierarchy_level=40) should only have read access."""
        self.auth_tenant(self.viewer_a, self.tenant_a)
        tickets_url = self.api_url("/tickets/tickets/")

        # POST — should be denied
        resp_create = self.client.post(tickets_url, {
            "subject": "Viewer tries to create",
            "description": "Should fail",
            "priority": "low",
            "status": str(self.status_open_a.pk),
        }, format="json")
        self.assertEqual(
            resp_create.status_code, 403,
            f"Viewer POST should return 403, got {resp_create.status_code}"
        )

        # Create a ticket for viewer to try to modify
        ticket = self.create_ticket(
            self.tenant_a, self.admin_a,
            subject="Viewer target", assignee=self.viewer_a,
        )
        detail_url = self.api_url(f"/tickets/tickets/{ticket.pk}/")

        # PATCH — should be denied
        resp_update = self.client.patch(
            detail_url, {"subject": "Viewer update"}, format="json"
        )
        self.assertEqual(
            resp_update.status_code, 403,
            f"Viewer PATCH should return 403, got {resp_update.status_code}"
        )

        # DELETE — should be denied
        resp_delete = self.client.delete(detail_url)
        self.assertEqual(
            resp_delete.status_code, 403,
            f"Viewer DELETE should return 403, got {resp_delete.status_code}"
        )

    # ------------------------------------------------------------------
    # 12.11 team-progress endpoint: agent -> 403, manager -> 200
    # ------------------------------------------------------------------
    def test_team_progress_requires_manager_or_above(self):
        """
        GET /api/v1/tickets/tickets/team-progress/ should return 403
        for agents and 200 for managers.
        """
        url = self.api_url("/tickets/tickets/team-progress/")

        # Agent should get 403
        self.auth_tenant(self.agent_a, self.tenant_a)
        resp_agent = self.client.get(url)
        self.assertEqual(
            resp_agent.status_code, 403,
            f"Agent accessing team-progress should get 403, got {resp_agent.status_code}"
        )

        # Manager should get 200
        self.auth_tenant(self.manager_a, self.tenant_a)
        resp_manager = self.client.get(url)
        self.assertEqual(
            resp_manager.status_code, 200,
            f"Manager accessing team-progress should get 200, got {resp_manager.status_code}"
        )
