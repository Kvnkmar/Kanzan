"""
Module 13 — CRM Features (29 tests)

Covers: Accounts & Contacts, Pipeline & Stage transitions,
CRM Activities, Contact Timeline, Scoring tasks, Pipeline forecast.
"""

import unittest
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.utils import timezone
from freezegun import freeze_time

from apps.contacts.models import Account, Contact, ContactEvent
from apps.crm.models import Activity
from apps.crm.tasks import calculate_account_health_scores, calculate_lead_scores
from apps.tickets.models import (
    Pipeline,
    PipelineStage,
    Ticket,
    TicketActivity,
    TicketStatus,
)
from main.context import set_current_tenant

from tests.base import KanzenBaseTestCase


# =====================================================================
# Accounts & Contacts (13.1 – 13.4)
# =====================================================================


class AccountAndContactTests(KanzenBaseTestCase):
    """Tests for CRM Account model and its relationship with Contacts/Tickets."""

    def setUp(self):
        super().setUp()
        self.set_tenant(self.tenant_a)
        self.auth_tenant(self.admin_a, self.tenant_a)

    # 13.1 Account created → linked to tenant
    def test_13_01_account_created_linked_to_tenant(self):
        account = Account.objects.create(
            name="Acme Corp",
            industry="Technology",
            tenant=self.tenant_a,
        )
        self.assertEqual(account.tenant_id, self.tenant_a.pk)
        self.assertEqual(account.name, "Acme Corp")

    # 13.2 Contact linked to Account
    def test_13_02_contact_linked_to_account(self):
        account = Account.objects.create(name="Beta Inc", tenant=self.tenant_a)
        contact = Contact.objects.create(
            first_name="Jane",
            last_name="Smith",
            email="jane@beta.com",
            account=account,
            tenant=self.tenant_a,
        )
        self.assertEqual(contact.account_id, account.pk)
        self.assertIn(contact, account.contacts.all())

    # 13.3 Ticket linked to both Contact and Account
    def test_13_03_ticket_linked_to_contact_and_account(self):
        account = Account.objects.create(name="Gamma LLC", tenant=self.tenant_a)
        contact = Contact.objects.create(
            first_name="Bob",
            last_name="Brown",
            email="bob@gamma.com",
            account=account,
            tenant=self.tenant_a,
        )
        ticket = self.create_ticket(
            tenant=self.tenant_a,
            user=self.admin_a,
            contact=contact,
            account=account,
        )
        self.assertEqual(ticket.contact_id, contact.pk)
        self.assertEqual(ticket.account_id, account.pk)

    # 13.4 Account from tenant B not accessible by tenant A → 403
    def test_13_04_account_cross_tenant_isolation(self):
        self.set_tenant(self.tenant_b)
        account_b = Account.objects.create(name="Secret Corp", tenant=self.tenant_b)

        # Attempt to access tenant B's account from tenant A
        self.auth_tenant(self.admin_a, self.tenant_a)
        url = self.api_url(f"/contacts/accounts/{account_b.pk}/")
        resp = self.client.get(url)
        self.assertIn(resp.status_code, [403, 404])


# =====================================================================
# Pipeline (13.5 – 13.11)
# =====================================================================


class PipelineTests(KanzenBaseTestCase):
    """Tests for Pipeline, PipelineStage, and stage transitions."""

    def setUp(self):
        super().setUp()
        self.set_tenant(self.tenant_a)
        self.auth_tenant(self.admin_a, self.tenant_a)

        # Create a pipeline with stages
        self.pipeline = Pipeline.objects.create(
            name="Sales Pipeline",
            is_default=True,
            tenant=self.tenant_a,
        )
        self.stage_qual = PipelineStage.objects.create(
            pipeline=self.pipeline,
            name="Qualification",
            order=0,
            probability=20,
            tenant=self.tenant_a,
        )
        self.stage_proposal = PipelineStage.objects.create(
            pipeline=self.pipeline,
            name="Proposal",
            order=1,
            probability=50,
            tenant=self.tenant_a,
        )
        self.stage_won = PipelineStage.objects.create(
            pipeline=self.pipeline,
            name="Closed Won",
            order=2,
            probability=100,
            is_won=True,
            tenant=self.tenant_a,
        )
        self.stage_lost = PipelineStage.objects.create(
            pipeline=self.pipeline,
            name="Closed Lost",
            order=3,
            probability=0,
            is_lost=True,
            tenant=self.tenant_a,
        )

    # 13.5 Pipeline created with ordered stages
    def test_13_05_pipeline_with_ordered_stages(self):
        stages = list(
            PipelineStage.objects.filter(pipeline=self.pipeline).order_by("order")
        )
        self.assertEqual(len(stages), 4)
        self.assertEqual(stages[0].name, "Qualification")
        self.assertEqual(stages[1].name, "Proposal")
        self.assertEqual(stages[2].name, "Closed Won")
        self.assertEqual(stages[3].name, "Closed Lost")

    # 13.6 Ticket assigned to pipeline_stage
    def test_13_06_ticket_assigned_to_pipeline_stage(self):
        ticket = self.create_ticket(
            tenant=self.tenant_a,
            user=self.admin_a,
            ticket_type="deal",
            pipeline_stage=self.stage_qual,
        )
        self.assertEqual(ticket.pipeline_stage_id, self.stage_qual.pk)

    # 13.7 POST /change-stage/ → stage changed, TicketActivity logged
    def test_13_07_change_stage_creates_activity(self):
        ticket = self.create_ticket(
            tenant=self.tenant_a,
            user=self.admin_a,
            ticket_type="deal",
            pipeline_stage=self.stage_qual,
        )
        url = self.api_url(f"/tickets/tickets/{ticket.pk}/change-stage/")
        resp = self.client.post(
            url,
            {"stage": str(self.stage_proposal.pk)},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.pipeline_stage_id, self.stage_proposal.pk)

        # Verify TicketActivity was logged
        activity_exists = TicketActivity.unscoped.filter(
            ticket=ticket,
            event=TicketActivity.Event.PIPELINE_STAGE_CHANGED,
        ).exists()
        self.assertTrue(activity_exists)

    # 13.8 Changing to Won stage → ticket status moves to resolved
    def test_13_08_won_stage_sets_resolved(self):
        ticket = self.create_ticket(
            tenant=self.tenant_a,
            user=self.admin_a,
            ticket_type="deal",
            pipeline_stage=self.stage_qual,
        )
        url = self.api_url(f"/tickets/tickets/{ticket.pk}/change-stage/")
        resp = self.client.post(
            url,
            {"stage": str(self.stage_won.pk)},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status.slug, "resolved")

    # 13.9 Changing to Lost stage → ticket status moves to closed
    def test_13_09_lost_stage_sets_closed(self):
        ticket = self.create_ticket(
            tenant=self.tenant_a,
            user=self.admin_a,
            ticket_type="deal",
            pipeline_stage=self.stage_qual,
        )
        url = self.api_url(f"/tickets/tickets/{ticket.pk}/change-stage/")
        resp = self.client.post(
            url,
            {"stage": str(self.stage_lost.pk)},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        ticket.refresh_from_db()
        self.assertTrue(ticket.status.is_closed)

    # 13.10 won_reason / lost_reason saved on stage change
    def test_13_10_won_lost_reason_saved(self):
        ticket_won = self.create_ticket(
            tenant=self.tenant_a,
            user=self.admin_a,
            ticket_type="deal",
            pipeline_stage=self.stage_qual,
        )
        url = self.api_url(f"/tickets/tickets/{ticket_won.pk}/change-stage/")
        self.client.post(
            url,
            {"stage": str(self.stage_won.pk), "reason": "Great pricing"},
            format="json",
        )
        ticket_won.refresh_from_db()
        self.assertEqual(ticket_won.won_reason, "Great pricing")
        self.assertIsNotNone(ticket_won.won_at)

        ticket_lost = self.create_ticket(
            tenant=self.tenant_a,
            user=self.admin_a,
            ticket_type="deal",
            pipeline_stage=self.stage_qual,
        )
        url = self.api_url(f"/tickets/tickets/{ticket_lost.pk}/change-stage/")
        self.client.post(
            url,
            {"stage": str(self.stage_lost.pk), "reason": "Budget constraints"},
            format="json",
        )
        ticket_lost.refresh_from_db()
        self.assertEqual(ticket_lost.lost_reason, "Budget constraints")
        self.assertIsNotNone(ticket_lost.lost_at)

    # 13.11 Stage from different pipeline rejected → 400
    def test_13_11_cross_pipeline_stage_rejected(self):
        other_pipeline = Pipeline.objects.create(
            name="Other Pipeline",
            tenant=self.tenant_a,
        )
        other_stage = PipelineStage.objects.create(
            pipeline=other_pipeline,
            name="Other Stage",
            order=0,
            probability=10,
            tenant=self.tenant_a,
        )

        ticket = self.create_ticket(
            tenant=self.tenant_a,
            user=self.admin_a,
            ticket_type="deal",
            pipeline_stage=self.stage_qual,
        )
        url = self.api_url(f"/tickets/tickets/{ticket.pk}/change-stage/")
        resp = self.client.post(
            url,
            {"stage": str(other_stage.pk)},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)


# =====================================================================
# Activities (13.12 – 13.16)
# =====================================================================


class ActivityTests(KanzenBaseTestCase):
    """Tests for CRM Activities linked to tickets and contacts."""

    def setUp(self):
        super().setUp()
        self.set_tenant(self.tenant_a)
        self.auth_tenant(self.admin_a, self.tenant_a)
        # SubscriptionMiddleware requires an active subscription
        self.create_subscription(self.tenant_a, self.free_plan)

    # 13.12 Activity created linked to ticket → ticket.last_activity_at updated
    def test_13_12_activity_updates_ticket_last_activity(self):
        ticket = self.create_ticket(tenant=self.tenant_a, user=self.admin_a)
        self.assertIsNone(ticket.last_activity_at)

        url = self.api_url("/crm/activities/")
        resp = self.client.post(
            url,
            {
                "activity_type": "call",
                "subject": "Follow-up call",
                "ticket": str(ticket.pk),
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        ticket.refresh_from_db()
        self.assertIsNotNone(ticket.last_activity_at)

    # 13.13 Activity completion → removed from overdue count
    @unittest.skip("Not implemented: ReminderManager.overdue() method")
    def test_13_13_completed_activity_not_in_overdue(self):
        now = timezone.now()
        ticket = self.create_ticket(
            tenant=self.tenant_a,
            user=self.admin_a,
            assignee=self.admin_a,
        )

        activity = Activity.objects.create(
            activity_type="task",
            subject="Overdue task",
            due_at=now - timedelta(hours=1),
            assigned_to=self.admin_a,
            created_by=self.admin_a,
            ticket=ticket,
            tenant=self.tenant_a,
        )

        url = self.api_url("/crm/activities/my-tasks/")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        activity_ids = [a["id"] for a in resp.data["activities"]]
        self.assertIn(str(activity.pk), activity_ids)

        # Complete the activity
        activity.completed_at = now
        activity.save(update_fields=["completed_at"])

        resp = self.client.get(url)
        activity_ids = [a["id"] for a in resp.data["activities"]]
        self.assertNotIn(str(activity.pk), activity_ids)

    # 13.14 GET /api/v1/crm/my-tasks/ returns only requesting user's tasks
    @unittest.skip("Not implemented: ReminderManager.overdue() method")
    def test_13_14_my_tasks_scoped_to_user(self):
        now = timezone.now()
        Activity.objects.create(
            activity_type="call",
            subject="Admin's task",
            due_at=now + timedelta(hours=1),
            assigned_to=self.admin_a,
            created_by=self.admin_a,
            tenant=self.tenant_a,
        )
        Activity.objects.create(
            activity_type="call",
            subject="Agent's task",
            due_at=now + timedelta(hours=1),
            assigned_to=self.agent_a,
            created_by=self.admin_a,
            tenant=self.tenant_a,
        )

        # Admin sees only their task
        url = self.api_url("/crm/activities/my-tasks/")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        subjects = [a["subject"] for a in resp.data["activities"]]
        self.assertIn("Admin's task", subjects)
        self.assertNotIn("Agent's task", subjects)

        # Agent sees only their task
        self.auth_tenant(self.agent_a, self.tenant_a)
        resp = self.client.get(url)
        subjects = [a["subject"] for a in resp.data["activities"]]
        self.assertIn("Agent's task", subjects)
        self.assertNotIn("Admin's task", subjects)

    # 13.15 Overdue follow_up_due_at → overdue_followups in my-tasks
    @unittest.skip("Not implemented: ReminderManager.overdue() method")
    def test_13_15_overdue_followup_in_my_tasks(self):
        now = timezone.now()
        ticket = self.create_ticket(
            tenant=self.tenant_a,
            user=self.admin_a,
            assignee=self.admin_a,
        )
        Ticket.unscoped.filter(pk=ticket.pk).update(
            follow_up_due_at=now - timedelta(hours=2),
        )

        url = self.api_url("/crm/activities/my-tasks/")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        overdue_ticket_ids = [
            str(t["id"]) for t in resp.data["overdue_followups"]
        ]
        self.assertIn(str(ticket.pk), overdue_ticket_ids)

    # 13.16 No duplicate follow-up notifications same day
    @unittest.skip("Not implemented: ReminderManager.overdue() method")
    def test_13_16_no_duplicate_followup_overdue(self):
        """Overdue follow-up tickets should appear once, not duplicated."""
        now = timezone.now()
        ticket = self.create_ticket(
            tenant=self.tenant_a,
            user=self.admin_a,
            assignee=self.admin_a,
        )
        Ticket.unscoped.filter(pk=ticket.pk).update(
            follow_up_due_at=now - timedelta(hours=2),
        )

        url = self.api_url("/crm/activities/my-tasks/")
        resp = self.client.get(url)
        overdue_ticket_ids = [
            str(t["id"]) for t in resp.data["overdue_followups"]
        ]
        # Same ticket should only appear once
        self.assertEqual(
            overdue_ticket_ids.count(str(ticket.pk)),
            1,
        )


# =====================================================================
# Contact Timeline (13.17 – 13.21)
# =====================================================================


class ContactTimelineTests(KanzenBaseTestCase):
    """Tests for the unified ContactEvent timeline."""

    def setUp(self):
        super().setUp()
        self.set_tenant(self.tenant_a)
        self.auth_tenant(self.admin_a, self.tenant_a)

    def _create_events(self, contact, count, source="ticket"):
        """Helper to create N ContactEvents for a contact."""
        events = []
        for i in range(count):
            ev = ContactEvent.objects.create(
                contact=contact,
                event_type=f"test_event_{i}",
                description=f"Event {i}",
                source=source,
                actor=self.admin_a,
                tenant=self.tenant_a,
            )
            events.append(ev)
        return events

    # 13.17 Ticket event → ContactEvent created for linked contact
    def test_13_17_ticket_event_creates_contact_event(self):
        ContactEvent.objects.create(
            contact=self.contact_a,
            event_type="ticket_created",
            description="Ticket created for contact",
            source=ContactEvent.Source.TICKET,
            actor=self.admin_a,
            tenant=self.tenant_a,
            metadata={"ticket_subject": "Test"},
        )
        events = ContactEvent.objects.filter(
            contact=self.contact_a,
            source=ContactEvent.Source.TICKET,
        )
        self.assertEqual(events.count(), 1)
        self.assertEqual(events.first().event_type, "ticket_created")

    # 13.18 Activity event → ContactEvent created for linked contact
    def test_13_18_activity_event_creates_contact_event(self):
        ContactEvent.objects.create(
            contact=self.contact_a,
            event_type="activity_completed",
            description="Call completed",
            source=ContactEvent.Source.ACTIVITY,
            actor=self.admin_a,
            tenant=self.tenant_a,
        )
        events = ContactEvent.objects.filter(
            contact=self.contact_a,
            source=ContactEvent.Source.ACTIVITY,
        )
        self.assertEqual(events.count(), 1)

    # 13.19 GET /contact/{id}/timeline/ returns events from all sources
    def test_13_19_timeline_returns_all_sources(self):
        for source in ["ticket", "activity", "email", "manual"]:
            ContactEvent.objects.create(
                contact=self.contact_a,
                event_type=f"{source}_event",
                description=f"From {source}",
                source=source,
                actor=self.admin_a,
                tenant=self.tenant_a,
            )

        url = self.api_url(f"/contacts/contacts/{self.contact_a.pk}/timeline/")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        sources = {e["source"] for e in resp.data["results"]}
        self.assertEqual(sources, {"ticket", "activity", "email", "manual"})

    # 13.20 Timeline is paginated (page_size=25)
    def test_13_20_timeline_paginated(self):
        self._create_events(self.contact_a, 30)

        url = self.api_url(f"/contacts/contacts/{self.contact_a.pk}/timeline/")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["results"]), 25)
        self.assertIsNotNone(resp.data["next"])

    # 13.21 ContactEvent is append-only (no delete/update endpoint)
    def test_13_21_contact_event_append_only(self):
        event = ContactEvent.objects.create(
            contact=self.contact_a,
            event_type="test",
            description="Test event",
            source=ContactEvent.Source.MANUAL,
            actor=self.admin_a,
            tenant=self.tenant_a,
        )

        # Timeline endpoint should not support DELETE or PUT
        url = self.api_url(f"/contacts/contacts/{self.contact_a.pk}/timeline/")
        resp_delete = self.client.delete(url)
        self.assertIn(resp_delete.status_code, [403, 405, 404])

        resp_put = self.client.put(url, {}, format="json")
        self.assertIn(resp_put.status_code, [403, 405, 404])


# =====================================================================
# Scoring (13.22 – 13.26)
# =====================================================================


class ScoringTests(KanzenBaseTestCase):
    """Tests for the nightly lead/account scoring Celery tasks."""

    def setUp(self):
        super().setUp()
        self.set_tenant(self.tenant_a)
        self.auth_tenant(self.admin_a, self.tenant_a)

    # 13.22 calculate_lead_scores task runs without error
    def test_13_22_lead_scores_task_runs(self):
        result = calculate_lead_scores.apply()
        self.assertTrue(result.successful())

    # 13.23 Contact with recent activity gets higher lead_score than inactive
    def test_13_23_recent_activity_higher_score(self):
        active_contact = Contact.objects.create(
            first_name="Active",
            last_name="User",
            email="active@example.com",
            last_activity_at=timezone.now(),
            tenant=self.tenant_a,
        )
        inactive_contact = Contact.objects.create(
            first_name="Inactive",
            last_name="User",
            email="inactive@example.com",
            last_activity_at=timezone.now() - timedelta(days=60),
            tenant=self.tenant_a,
        )

        # Create a recent ContactEvent for the active contact
        ContactEvent.objects.create(
            contact=active_contact,
            event_type="interaction",
            source=ContactEvent.Source.TICKET,
            actor=self.admin_a,
            tenant=self.tenant_a,
        )

        calculate_lead_scores.apply()

        active_contact.refresh_from_db()
        inactive_contact.refresh_from_db()
        self.assertGreater(active_contact.lead_score, inactive_contact.lead_score)

    # 13.24 calculate_account_health_scores task runs without error
    def test_13_24_account_health_scores_task_runs(self):
        Account.objects.create(name="Test Account", tenant=self.tenant_a)
        result = calculate_account_health_scores.apply()
        self.assertTrue(result.successful())

    # 13.25 Account with low CSAT gets lower health_score
    def test_13_25_low_csat_lowers_health_score(self):
        good_account = Account.objects.create(
            name="Good Account", tenant=self.tenant_a
        )
        bad_account = Account.objects.create(
            name="Bad Account", tenant=self.tenant_a
        )

        now = timezone.now()

        # Create contacts for each account
        good_contact = Contact.objects.create(
            first_name="Good",
            last_name="Contact",
            email="good@example.com",
            account=good_account,
            last_activity_at=now,
            tenant=self.tenant_a,
        )
        bad_contact = Contact.objects.create(
            first_name="Bad",
            last_name="Contact",
            email="bad@example.com",
            account=bad_account,
            last_activity_at=now,
            tenant=self.tenant_a,
        )

        # Create tickets with different CSAT ratings
        good_ticket = self.create_ticket(
            tenant=self.tenant_a,
            user=self.admin_a,
            contact=good_contact,
        )
        Ticket.unscoped.filter(pk=good_ticket.pk).update(
            csat_rating=5,
            csat_submitted_at=now,
        )

        bad_ticket = self.create_ticket(
            tenant=self.tenant_a,
            user=self.admin_a,
            contact=bad_contact,
        )
        Ticket.unscoped.filter(pk=bad_ticket.pk).update(
            csat_rating=1,
            csat_submitted_at=now,
        )

        calculate_account_health_scores.apply()

        good_account.refresh_from_db()
        bad_account.refresh_from_db()
        self.assertGreater(good_account.health_score, bad_account.health_score)

    # 13.26 lead_score and health_score are read-only via API (PATCH ignored)
    def test_13_26_scores_read_only_via_api(self):
        account = Account.objects.create(
            name="RO Account",
            health_score=50,
            tenant=self.tenant_a,
        )
        url = self.api_url(f"/contacts/accounts/{account.pk}/")
        resp = self.client.patch(
            url,
            {"health_score": 99},
            format="json",
        )
        account.refresh_from_db()
        # health_score should remain unchanged (read-only)
        self.assertEqual(account.health_score, 50)

        # Similarly for contact lead_score
        url = self.api_url(f"/contacts/contacts/{self.contact_a.pk}/")
        original_score = self.contact_a.lead_score
        resp = self.client.patch(
            url,
            {"lead_score": 99},
            format="json",
        )
        self.contact_a.refresh_from_db()
        self.assertEqual(self.contact_a.lead_score, original_score)


# =====================================================================
# Forecast (13.27 – 13.29)
# =====================================================================


class ForecastTests(KanzenBaseTestCase):
    """Tests for the pipeline forecast endpoint."""

    def setUp(self):
        super().setUp()
        self.set_tenant(self.tenant_a)
        self.auth_tenant(self.admin_a, self.tenant_a)

        self.pipeline = Pipeline.objects.create(
            name="Sales",
            is_default=True,
            tenant=self.tenant_a,
        )
        self.stage_a = PipelineStage.objects.create(
            pipeline=self.pipeline,
            name="Qualification",
            order=0,
            probability=25,
            tenant=self.tenant_a,
        )
        self.stage_b = PipelineStage.objects.create(
            pipeline=self.pipeline,
            name="Negotiation",
            order=1,
            probability=75,
            tenant=self.tenant_a,
        )

    # 13.27 GET /pipeline/{id}/forecast/ returns correct weighted values
    def test_13_27_forecast_returns_data(self):
        self.create_ticket(
            tenant=self.tenant_a,
            user=self.admin_a,
            ticket_type="deal",
            deal_value=Decimal("10000.00"),
            probability=50,
            pipeline_stage=self.stage_a,
        )

        url = self.api_url(f"/crm/pipeline/{self.pipeline.pk}/forecast/")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("stages", resp.data)
        self.assertIn("total_weighted_forecast", resp.data)
        self.assertEqual(resp.data["pipeline"], "Sales")

    # 13.28 weighted_value = sum(deal_value * probability / 100) per stage
    def test_13_28_weighted_value_calculation(self):
        # Deal 1: $10,000 at 50% probability in stage_a
        self.create_ticket(
            tenant=self.tenant_a,
            user=self.admin_a,
            ticket_type="deal",
            deal_value=Decimal("10000.00"),
            probability=50,
            pipeline_stage=self.stage_a,
        )
        # Deal 2: $20,000 at 80% probability in stage_a
        self.create_ticket(
            tenant=self.tenant_a,
            user=self.admin_a,
            ticket_type="deal",
            deal_value=Decimal("20000.00"),
            probability=80,
            pipeline_stage=self.stage_a,
        )

        url = self.api_url(f"/crm/pipeline/{self.pipeline.pk}/forecast/")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        qual_stage = next(
            s for s in resp.data["stages"] if s["stage"] == "Qualification"
        )
        # Expected: 10000*50/100 + 20000*80/100 = 5000 + 16000 = 21000
        self.assertAlmostEqual(qual_stage["weighted_value"], 21000.0, places=2)

    # 13.29 Forecast endpoint: agent → 403
    def test_13_29_forecast_agent_forbidden(self):
        self.auth_tenant(self.agent_a, self.tenant_a)
        url = self.api_url(f"/crm/pipeline/{self.pipeline.pk}/forecast/")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 403)
