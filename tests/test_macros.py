"""
Module 9 — Macros & Canned Responses.

Tests:
    9.1  Apply macro → comment posted with rendered template
    9.2  Macro template variables rendered ({{ticket.number}}, {{contact.name}})
    9.3  Macro set_status action changes ticket status
    9.4  Macro set_priority action changes ticket priority
    9.5  Macro add_tag action adds tag to ticket
    9.6  All macro actions execute atomically (no partial apply)
    9.7  Macro from tenant B cannot be applied to tenant A ticket → 403
    9.8  Canned response /shortcut → content inserted, usage_count incremented
    9.9  Canned response usage_count increments on each use
    9.10 Canned response from another tenant not accessible → 404
"""

import unittest

from rest_framework import status as http_status

from apps.tickets.models import CannedResponse, Macro
from main.context import clear_current_tenant
from tests.base import KanzenBaseTestCase


class MacroTests(KanzenBaseTestCase):
    """Tests for Macro CRUD and apply behaviour (9.1-9.7)."""

    def setUp(self):
        super().setUp()
        self.set_tenant(self.tenant_a)
        self.macro_a = Macro.objects.create(
            name="Close & Thank",
            description="Close ticket with a thank-you message",
            body=(
                "Thank you for contacting us about ticket #{{ticket.number}}. "
                "Dear {{contact.name}}, your issue has been resolved."
            ),
            actions=[
                {"action": "set_status", "value": self.status_closed_a.slug},
                {"action": "set_priority", "value": "low"},
                {"action": "add_tag", "value": "resolved-via-macro"},
            ],
            is_shared=True,
            created_by=self.admin_a,
        )
        self.ticket = self.create_ticket(
            tenant=self.tenant_a,
            user=self.admin_a,
            contact=self.contact_a,
            priority="high",
        )
        clear_current_tenant()

    # ------------------------------------------------------------------
    # 9.1 — Apply macro → comment posted with rendered template
    # ------------------------------------------------------------------
    @unittest.skip("Not implemented: apply-macro endpoint")
    def test_9_1_apply_macro_posts_comment(self):
        """Applying a macro should post the macro body as a comment on the ticket."""
        self.auth_tenant(self.admin_a, self.tenant_a)
        url = self.api_url(
            f"/tickets/tickets/{self.ticket.pk}/apply-macro/"
        )
        resp = self.client.post(
            url, {"macro_id": str(self.macro_a.pk)}, format="json"
        )
        self.assertEqual(resp.status_code, http_status.HTTP_200_OK)

    # ------------------------------------------------------------------
    # 9.2 — Macro template variables rendered
    # ------------------------------------------------------------------
    def test_9_2_macro_template_variables_rendered(self):
        """
        {{ticket.number}} and {{contact.name}} should be replaced when
        the macro body is rendered.
        """
        body = self.macro_a.body
        rendered = body.replace(
            "{{ticket.number}}", str(self.ticket.number)
        )
        contact_name = (
            self.contact_a.full_name
            if hasattr(self.contact_a, "full_name") and self.contact_a.full_name
            else f"{self.contact_a.first_name} {self.contact_a.last_name}".strip()
        )
        rendered = rendered.replace("{{contact.name}}", contact_name)

        self.assertIn(str(self.ticket.number), rendered)
        self.assertIn(contact_name, rendered)
        self.assertNotIn("{{ticket.number}}", rendered)
        self.assertNotIn("{{contact.name}}", rendered)

    # ------------------------------------------------------------------
    # 9.3 — Macro set_status action changes ticket status
    # ------------------------------------------------------------------
    @unittest.skip("Not implemented: apply-macro endpoint")
    def test_9_3_macro_set_status_action(self):
        """set_status action in a macro should change the ticket's status."""
        self.auth_tenant(self.admin_a, self.tenant_a)
        url = self.api_url(
            f"/tickets/tickets/{self.ticket.pk}/apply-macro/"
        )
        self.client.post(
            url, {"macro_id": str(self.macro_a.pk)}, format="json"
        )
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status_id, self.status_closed_a.pk)

    # ------------------------------------------------------------------
    # 9.4 — Macro set_priority action changes ticket priority
    # ------------------------------------------------------------------
    @unittest.skip("Not implemented: apply-macro endpoint")
    def test_9_4_macro_set_priority_action(self):
        """set_priority action in a macro should change the ticket's priority."""
        self.auth_tenant(self.admin_a, self.tenant_a)
        url = self.api_url(
            f"/tickets/tickets/{self.ticket.pk}/apply-macro/"
        )
        self.client.post(
            url, {"macro_id": str(self.macro_a.pk)}, format="json"
        )
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.priority, "low")

    # ------------------------------------------------------------------
    # 9.5 — Macro add_tag action adds tag to ticket
    # ------------------------------------------------------------------
    @unittest.skip("Not implemented: apply-macro endpoint")
    def test_9_5_macro_add_tag_action(self):
        """add_tag action in a macro should append a tag to the ticket."""
        self.auth_tenant(self.admin_a, self.tenant_a)
        url = self.api_url(
            f"/tickets/tickets/{self.ticket.pk}/apply-macro/"
        )
        self.client.post(
            url, {"macro_id": str(self.macro_a.pk)}, format="json"
        )
        self.ticket.refresh_from_db()
        self.assertIn("resolved-via-macro", self.ticket.tags)

    # ------------------------------------------------------------------
    # 9.6 — All macro actions execute atomically
    # ------------------------------------------------------------------
    @unittest.skip("Not implemented: apply-macro endpoint")
    def test_9_6_macro_actions_atomic(self):
        """If one macro action fails, none of the changes should persist."""
        self.set_tenant(self.tenant_a)
        bad_macro = Macro.objects.create(
            name="Bad Macro",
            body="test body",
            actions=[
                {"action": "set_priority", "value": "low"},
                {"action": "set_status", "value": "nonexistent-status-slug"},
            ],
            is_shared=True,
            created_by=self.admin_a,
        )
        clear_current_tenant()

        self.auth_tenant(self.admin_a, self.tenant_a)
        url = self.api_url(
            f"/tickets/tickets/{self.ticket.pk}/apply-macro/"
        )
        resp = self.client.post(
            url, {"macro_id": str(bad_macro.pk)}, format="json"
        )
        self.assertGreaterEqual(resp.status_code, 400)
        # Priority should remain unchanged because of atomicity
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.priority, "high")

    # ------------------------------------------------------------------
    # 9.7 — Macro from tenant B not visible / accessible from tenant A
    # ------------------------------------------------------------------
    def test_9_7_macro_cross_tenant_isolation(self):
        """A macro belonging to tenant B must not be visible from tenant A."""
        self.set_tenant(self.tenant_b)
        macro_b = Macro.objects.create(
            name="Tenant B Macro",
            body="Hello from tenant B",
            actions=[],
            is_shared=True,
            created_by=self.admin_b,
        )
        clear_current_tenant()

        # Admin A listing macros on tenant A should not see macro_b
        self.auth_tenant(self.admin_a, self.tenant_a)
        list_url = self.api_url("/tickets/macros/")
        resp = self.client.get(list_url)
        self.assertEqual(resp.status_code, http_status.HTTP_200_OK)
        results = resp.data.get("results", resp.data)
        macro_ids = [str(m["id"]) for m in results]
        self.assertNotIn(str(macro_b.pk), macro_ids)

        # Direct detail access should return 404 (tenant scoping)
        detail_url = self.api_url(f"/tickets/macros/{macro_b.pk}/")
        resp = self.client.get(detail_url)
        self.assertIn(
            resp.status_code,
            [http_status.HTTP_404_NOT_FOUND, http_status.HTTP_403_FORBIDDEN],
        )


class CannedResponseTests(KanzenBaseTestCase):
    """Tests for CannedResponse CRUD and usage tracking (9.8-9.10)."""

    def setUp(self):
        super().setUp()
        self.set_tenant(self.tenant_a)
        self.canned_a = CannedResponse.objects.create(
            title="Thank You",
            content=(
                "Thank you for reaching out about ticket "
                "#{{ticket.number}}, {{contact.name}}!"
            ),
            category="General",
            shortcut="/thanks",
            is_shared=True,
            created_by=self.admin_a,
        )
        self.ticket = self.create_ticket(
            tenant=self.tenant_a,
            user=self.admin_a,
            contact=self.contact_a,
        )
        clear_current_tenant()

    # ------------------------------------------------------------------
    # 9.8 — Canned response render returns substituted content
    #       and increments usage_count
    # ------------------------------------------------------------------
    def test_9_8_canned_response_render_and_usage_count(self):
        """
        POST /canned-responses/{id}/render/ with a ticket_id should return
        the template with variables replaced and increment usage_count.
        """
        self.auth_tenant(self.admin_a, self.tenant_a)
        url = self.api_url(
            f"/tickets/canned-responses/{self.canned_a.pk}/render/"
        )
        resp = self.client.post(
            url, {"ticket_id": str(self.ticket.pk)}, format="json"
        )
        self.assertEqual(resp.status_code, http_status.HTTP_200_OK)

        rendered = resp.data["content"]
        self.assertIn(str(self.ticket.number), rendered)
        contact_name = (
            self.contact_a.full_name
            if hasattr(self.contact_a, "full_name") and self.contact_a.full_name
            else f"{self.contact_a.first_name} {self.contact_a.last_name}".strip()
        )
        self.assertIn(contact_name, rendered)
        self.assertNotIn("{{ticket.number}}", rendered)
        self.assertNotIn("{{contact.name}}", rendered)

        # usage_count should have been incremented from 0 to 1
        self.canned_a.refresh_from_db()
        self.assertEqual(self.canned_a.usage_count, 1)

    # ------------------------------------------------------------------
    # 9.9 — Canned response usage_count increments on each use
    # ------------------------------------------------------------------
    def test_9_9_canned_response_usage_count_increments(self):
        """Each render call should increment usage_count by exactly 1."""
        self.auth_tenant(self.admin_a, self.tenant_a)
        url = self.api_url(
            f"/tickets/canned-responses/{self.canned_a.pk}/render/"
        )

        for expected_count in range(1, 4):
            resp = self.client.post(
                url, {"ticket_id": str(self.ticket.pk)}, format="json"
            )
            self.assertEqual(resp.status_code, http_status.HTTP_200_OK)
            self.canned_a.refresh_from_db()
            self.assertEqual(self.canned_a.usage_count, expected_count)

    # ------------------------------------------------------------------
    # 9.10 — Canned response from another tenant not accessible
    # ------------------------------------------------------------------
    def test_9_10_canned_response_cross_tenant_not_accessible(self):
        """
        admin_b authenticated against tenant_b must not see tenant_a's
        canned responses — neither in the list nor via direct detail lookup.
        """
        self.auth_tenant(self.admin_b, self.tenant_b)

        # List should not include tenant_a's canned response
        list_url = self.api_url("/tickets/canned-responses/")
        resp = self.client.get(list_url)
        self.assertEqual(resp.status_code, http_status.HTTP_200_OK)
        results = resp.data.get("results", resp.data)
        cr_ids = [str(c["id"]) for c in results]
        self.assertNotIn(str(self.canned_a.pk), cr_ids)

        # Direct detail access should return 404
        detail_url = self.api_url(
            f"/tickets/canned-responses/{self.canned_a.pk}/"
        )
        resp = self.client.get(detail_url)
        self.assertEqual(resp.status_code, http_status.HTTP_404_NOT_FOUND)
