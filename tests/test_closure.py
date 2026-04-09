"""
Module 15 — Auto-Close & Post-Closure Tests

Tests for:
- Auto-close task scheduling on ticket resolve
- Task ID persistence and idempotency guard
- Auto-close execution and TicketActivity logging
- Reopen-before-timer scenario (task ID mismatch)
- KB coverage check on ticket closure
- Ticket search (lookup) returning closed tickets
- Agent visibility restrictions on closed ticket search
"""

import unittest
from unittest.mock import patch, MagicMock

from django.utils import timezone

from apps.knowledge.models import Article, Category as KBCategory
from apps.tickets.models import Ticket, TicketActivity
from apps.tickets.tasks import auto_close_ticket

from tests.base import KanzenBaseTestCase


class TestAutoCloseScheduling(KanzenBaseTestCase):
    """15.1 - 15.2: Resolving a ticket schedules auto_close_ticket and saves the task ID."""

    def setUp(self):
        super().setUp()
        self.auth_tenant(self.admin_a, self.tenant_a)
        self.ticket = self.create_ticket(
            tenant=self.tenant_a,
            user=self.admin_a,
            subject="Auto-close scheduling test",
        )

    @patch("apps.tickets.tasks.auto_close_ticket")
    def test_15_1_resolve_schedules_auto_close_task(self, mock_task):
        """Resolving a ticket triggers the auto_close_ticket task via apply_async."""
        mock_result = MagicMock()
        mock_result.id = "fake-task-id-001"
        mock_task.apply_async.return_value = mock_result

        url = self.api_url(f"/tickets/tickets/{self.ticket.pk}/change-status/")
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(url, {"status": str(self.status_resolved_a.pk)})
        self.assertEqual(resp.status_code, 200)

        # auto_close_ticket.apply_async should have been called
        mock_task.apply_async.assert_called_once()
        call_args = mock_task.apply_async.call_args
        self.assertEqual(call_args.kwargs.get("args") or call_args[1].get("args") or call_args[0][0],
                         [str(self.ticket.pk)])

    @patch("apps.tickets.tasks.auto_close_ticket")
    def test_15_2_task_id_saved_to_ticket(self, mock_task):
        """The Celery task ID is persisted to ticket.auto_close_task_id."""
        mock_result = MagicMock()
        mock_result.id = "fake-task-id-002"
        mock_task.apply_async.return_value = mock_result

        url = self.api_url(f"/tickets/tickets/{self.ticket.pk}/change-status/")
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(url, {"status": str(self.status_resolved_a.pk)})
        self.assertEqual(resp.status_code, 200)

        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.auto_close_task_id, "fake-task-id-002")


class TestAutoCloseExecution(KanzenBaseTestCase):
    """15.3 - 15.5: Auto-close task execution, reopen guard, and activity logging."""

    def setUp(self):
        super().setUp()
        self.auth_tenant(self.admin_a, self.tenant_a)
        self.ticket = self.create_ticket(
            tenant=self.tenant_a,
            user=self.admin_a,
            subject="Auto-close execution test",
        )
        # Move ticket to resolved status directly via model
        self.set_tenant(self.tenant_a)
        self.ticket.status = self.status_resolved_a
        self.ticket.resolved_at = timezone.now()
        self.ticket.save()

    def test_15_3_auto_close_task_closes_ticket(self):
        """When the auto_close_ticket task runs, the ticket transitions to closed."""
        self.set_tenant(self.tenant_a)
        # Set a matching task ID so the guard passes
        task_id = "test-task-id-close"
        Ticket.unscoped.filter(pk=self.ticket.pk).update(auto_close_task_id=task_id)

        # Run the task via apply() which sets up the Celery request context
        result = auto_close_ticket.apply(
            args=[str(self.ticket.pk)],
            task_id=task_id,
        )

        self.ticket.refresh_from_db()
        self.assertTrue(self.ticket.status.is_closed)

    def test_15_4_reopen_before_timer_prevents_auto_close(self):
        """If the ticket is reopened, the old auto_close task becomes a no-op."""
        self.set_tenant(self.tenant_a)
        old_task_id = "old-task-id-reopen"
        Ticket.unscoped.filter(pk=self.ticket.pk).update(auto_close_task_id=old_task_id)

        # Reopen the ticket (change status back to open)
        self.ticket.refresh_from_db()
        self.ticket.status = self.status_open_a
        self.ticket.auto_close_task_id = "new-task-id-after-reopen"
        self.ticket.save()

        # Run the old task — should be a no-op because:
        #   1. Ticket is no longer "resolved"
        #   2. Task ID doesn't match
        auto_close_ticket.apply(
            args=[str(self.ticket.pk)],
            task_id=old_task_id,
        )

        self.ticket.refresh_from_db()
        # Ticket should still be open, not closed
        self.assertEqual(self.ticket.status.slug, "open")

    def test_15_5_auto_close_logs_activity(self):
        """Auto-close creates a TicketActivity with event=AUTO_CLOSED."""
        self.set_tenant(self.tenant_a)
        task_id = "test-task-id-activity"
        Ticket.unscoped.filter(pk=self.ticket.pk).update(auto_close_task_id=task_id)

        auto_close_ticket.apply(
            args=[str(self.ticket.pk)],
            task_id=task_id,
        )

        # Check for AUTO_CLOSED activity
        activity = TicketActivity.unscoped.filter(
            ticket=self.ticket,
            event=TicketActivity.Event.AUTO_CLOSED,
        )
        self.assertTrue(activity.exists(), "Expected an AUTO_CLOSED TicketActivity entry.")


class TestKBCoverageCheck(KanzenBaseTestCase):
    """15.6 - 15.7: KB coverage check sets needs_kb_article on ticket closure."""

    def setUp(self):
        super().setUp()
        self.auth_tenant(self.admin_a, self.tenant_a)
        self.set_tenant(self.tenant_a)

        # Create a KB category matching the ticket's category name
        self.kb_category = KBCategory(
            name="Billing",
            tenant=self.tenant_a,
        )
        self.kb_category.save()

    def _close_ticket_with_category(self, category_name):
        """Helper: create a ticket with a category and close it via the service layer."""
        ticket = self.create_ticket(
            tenant=self.tenant_a,
            user=self.admin_a,
            subject="KB coverage test",
            category=category_name,
        )
        # Close the ticket via the change_ticket_status service.
        # Use captureOnCommitCallbacks so that the ticket_closed signal
        # (deferred via transaction.on_commit) actually fires.
        from apps.tickets.services import change_ticket_status
        self.set_tenant(self.tenant_a)
        with self.captureOnCommitCallbacks(execute=True):
            change_ticket_status(ticket, self.status_closed_a, actor=self.admin_a)
        ticket.refresh_from_db()
        return ticket

    def test_15_6_category_with_few_articles_sets_needs_kb_article(self):
        """Category with <3 published KB articles sets needs_kb_article=True on close."""
        self.set_tenant(self.tenant_a)
        # Create only 2 published articles for "Billing"
        for i in range(2):
            Article(
                title=f"Billing Article {i}",
                content="Content",
                status="published",
                category=self.kb_category,
                author=self.admin_a,
                tenant=self.tenant_a,
            ).save()

        ticket = self._close_ticket_with_category("Billing")
        self.assertTrue(
            ticket.needs_kb_article,
            "Ticket should be flagged for KB article creation when category has <3 articles.",
        )

    def test_15_7_category_with_enough_articles_no_flag(self):
        """Category with 3+ published KB articles does NOT set needs_kb_article."""
        self.set_tenant(self.tenant_a)
        # Create 3 published articles for "Billing"
        for i in range(3):
            Article(
                title=f"Billing Guide {i}",
                content="Guide content",
                status="published",
                category=self.kb_category,
                author=self.admin_a,
                tenant=self.tenant_a,
            ).save()

        ticket = self._close_ticket_with_category("Billing")
        self.assertFalse(
            ticket.needs_kb_article,
            "Ticket should NOT be flagged when category has >= 3 published articles.",
        )


class TestTicketSearchClosed(KanzenBaseTestCase):
    """15.8 - 15.10: Ticket lookup returns closed tickets, with agent restrictions."""

    def setUp(self):
        super().setUp()
        self.set_tenant(self.tenant_a)
        # Create and close a ticket
        self.closed_ticket = self.create_ticket(
            tenant=self.tenant_a,
            user=self.admin_a,
            subject="Closed ticket for search",
            assignee=self.admin_a,
        )
        self.closed_ticket.status = self.status_closed_a
        self.closed_ticket.closed_at = timezone.now()
        self.closed_ticket.resolved_at = timezone.now()
        self.closed_ticket.save()
        self.closed_ticket.refresh_from_db()

    def test_15_8_search_by_number_returns_closed_tickets(self):
        """GET /api/v1/tickets/tickets/lookup/?number=N returns closed tickets."""
        self.auth_tenant(self.admin_a, self.tenant_a)
        url = self.api_url(
            f"/tickets/tickets/lookup/?number={self.closed_ticket.number}"
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        results = resp.data
        ticket_ids = [str(t["id"]) for t in results]
        self.assertIn(
            str(self.closed_ticket.pk),
            ticket_ids,
            "Closed ticket should appear in lookup results.",
        )

    @unittest.skip("Not implemented: search result InboundEmail linking")
    def test_15_9_search_includes_linked_inbound_email(self):
        """Search results include linked InboundEmail records."""
        pass

    def test_15_10_agent_cannot_list_unrelated_closed_ticket(self):
        """Agent cannot list a closed ticket they were not assigned to or did not create.

        Note: the lookup endpoint (search by number) intentionally shows all
        tenant tickets so agents can reference case numbers. The standard list
        endpoint enforces agent-level row filtering.
        """
        self.set_tenant(self.tenant_a)
        # Create a closed ticket NOT assigned to agent_a and NOT created by agent_a
        unrelated_ticket = self.create_ticket(
            tenant=self.tenant_a,
            user=self.admin_a,
            subject="Unrelated closed ticket",
            assignee=self.manager_a,
        )
        unrelated_ticket.status = self.status_closed_a
        unrelated_ticket.closed_at = timezone.now()
        unrelated_ticket.resolved_at = timezone.now()
        unrelated_ticket.save()
        unrelated_ticket.refresh_from_db()

        # Agent lists all tickets via the standard list endpoint
        self.auth_tenant(self.agent_a, self.tenant_a)
        url = self.api_url("/tickets/tickets/")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        # Agent should not see unrelated ticket in the list view
        results = resp.data.get("results", resp.data)
        ticket_ids = [str(t["id"]) for t in results]
        self.assertNotIn(
            str(unrelated_ticket.pk),
            ticket_ids,
            "Agent should not list a closed ticket they are not assigned to.",
        )
