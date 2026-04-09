"""
Module 8 — CSAT Survey Tests (9 tests).

Covers CSAT survey scheduling, submission, token validation, and idempotency.
"""

import unittest
from unittest.mock import patch, MagicMock

from django.core import signing
from django.utils import timezone
from freezegun import freeze_time

from apps.tickets.models import Ticket, TicketActivity
from main.context import set_current_tenant

from tests.base import KanzenBaseTestCase


class CSATSurveyTests(KanzenBaseTestCase):
    """Tests 8.1 – 8.9: CSAT survey lifecycle."""

    def setUp(self):
        super().setUp()
        self.set_tenant(self.tenant_a)

    def _resolve_ticket(self, ticket):
        """Move a ticket to resolved status via the service layer."""
        from apps.tickets.services import change_ticket_status

        set_current_tenant(self.tenant_a)
        change_ticket_status(ticket, self.status_resolved_a, actor=self.admin_a)
        ticket.refresh_from_db()

    def _generate_csat_token(self, ticket):
        """Generate a valid CSAT token matching the production format."""
        return signing.dumps(
            {"t": str(ticket.pk), "n": str(self.tenant_a.pk)},
            salt="csat",
        )

    # 8.1 ------------------------------------------------------------------
    @freeze_time("2026-04-05 10:00:00")
    @patch("apps.tickets.services._schedule_csat_survey")
    def test_8_1_ticket_resolved_queues_csat_survey(self, mock_schedule):
        """8.1 Ticket resolved -> CSAT survey email task queued."""
        ticket = self.create_ticket(
            self.tenant_a, self.admin_a, contact=self.contact_a,
        )

        with self.captureOnCommitCallbacks(execute=True):
            self._resolve_ticket(ticket)

        mock_schedule.assert_called_once_with(
            str(ticket.pk), str(self.tenant_a.pk),
        )

    # 8.2 ------------------------------------------------------------------
    @freeze_time("2026-04-05 10:00:00")
    def test_8_2_csat_not_sent_if_contact_has_no_email(self):
        """8.2 CSAT not sent if contact has no email."""
        from apps.tickets.tasks import send_csat_survey_email

        # Create contact without email
        from apps.contacts.models import Contact
        no_email_contact = Contact(
            first_name="NoEmail",
            last_name="Contact",
            email="",
            tenant=self.tenant_a,
        )
        no_email_contact.save()

        ticket = self.create_ticket(
            self.tenant_a, self.admin_a,
            contact=no_email_contact,
            status=self.status_resolved_a,
        )

        with patch("django.core.mail.send_mail") as mock_mail:
            send_csat_survey_email.apply(
                args=[str(ticket.pk), str(self.tenant_a.pk)],
            )
            mock_mail.assert_not_called()

    # 8.3 ------------------------------------------------------------------
    @freeze_time("2026-04-05 10:00:00")
    def test_8_3_csat_not_sent_if_ticket_reopened(self):
        """8.3 CSAT not sent if ticket reopened before task fires."""
        from apps.tickets.tasks import send_csat_survey_email
        from apps.tickets.services import change_ticket_status

        ticket = self.create_ticket(
            self.tenant_a, self.admin_a, contact=self.contact_a,
        )

        # Resolve then reopen
        self._resolve_ticket(ticket)
        change_ticket_status(ticket, self.status_open_a, actor=self.admin_a)
        ticket.refresh_from_db()

        self.assertNotEqual(ticket.status.slug, "resolved")

        with patch("django.core.mail.send_mail") as mock_mail:
            send_csat_survey_email.apply(
                args=[str(ticket.pk), str(self.tenant_a.pk)],
            )
            mock_mail.assert_not_called()

    # 8.4 ------------------------------------------------------------------
    @freeze_time("2026-04-05 10:00:00")
    def test_8_4_csat_not_sent_if_already_submitted(self):
        """8.4 CSAT not sent if already submitted (idempotency)."""
        from apps.tickets.tasks import send_csat_survey_email

        ticket = self.create_ticket(
            self.tenant_a, self.admin_a,
            contact=self.contact_a,
            status=self.status_resolved_a,
        )
        # Simulate already-submitted CSAT
        Ticket.unscoped.filter(pk=ticket.pk).update(csat_rating=5)

        with patch("django.core.mail.send_mail") as mock_mail:
            send_csat_survey_email.apply(
                args=[str(ticket.pk), str(self.tenant_a.pk)],
            )
            mock_mail.assert_not_called()

    # 8.5 ------------------------------------------------------------------
    @freeze_time("2026-04-05 10:00:00")
    def test_8_5_contact_submits_valid_rating(self):
        """8.5 Contact submits rating 1-5 -> csat fields saved."""
        ticket = self.create_ticket(
            self.tenant_a, self.admin_a,
            contact=self.contact_a,
            status=self.status_resolved_a,
        )
        token = self._generate_csat_token(ticket)

        resp = self.client.post(
            "/api/v1/tickets/csat/",
            {"token": token, "rating": 4, "comment": "Great service!"},
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["rating"], 4)

        ticket.refresh_from_db()
        self.assertEqual(ticket.csat_rating, 4)
        self.assertEqual(ticket.csat_comment, "Great service!")
        self.assertIsNotNone(ticket.csat_submitted_at)

    # 8.6 ------------------------------------------------------------------
    @freeze_time("2026-04-05 10:00:00")
    def test_8_6_invalid_rating_returns_400(self):
        """8.6 Invalid rating (0 or 6) -> 400."""
        ticket = self.create_ticket(
            self.tenant_a, self.admin_a,
            contact=self.contact_a,
            status=self.status_resolved_a,
        )
        token = self._generate_csat_token(ticket)

        # Rating 0 (below minimum)
        resp_low = self.client.post(
            "/api/v1/tickets/csat/",
            {"token": token, "rating": 0},
            format="json",
        )
        self.assertEqual(resp_low.status_code, 400)

        # Rating 6 (above maximum)
        resp_high = self.client.post(
            "/api/v1/tickets/csat/",
            {"token": token, "rating": 6},
            format="json",
        )
        self.assertEqual(resp_high.status_code, 400)

        # Verify nothing was saved
        ticket.refresh_from_db()
        self.assertIsNone(ticket.csat_rating)

    # 8.7 ------------------------------------------------------------------
    @freeze_time("2026-04-05 10:00:00")
    def test_8_7_ticket_activity_csat_received_logged(self):
        """8.7 TicketActivity 'csat_received' logged on submission."""
        ticket = self.create_ticket(
            self.tenant_a, self.admin_a,
            contact=self.contact_a,
            status=self.status_resolved_a,
        )
        token = self._generate_csat_token(ticket)

        self.client.post(
            "/api/v1/tickets/csat/",
            {"token": token, "rating": 5, "comment": "Excellent!"},
            format="json",
        )

        activity = TicketActivity.unscoped.filter(
            ticket=ticket,
            event=TicketActivity.Event.CSAT_RECEIVED,
        ).first()

        self.assertIsNotNone(activity)
        self.assertIn("5/5", activity.message)
        self.assertEqual(activity.metadata["rating"], 5)
        self.assertEqual(activity.metadata["comment"], "Excellent!")

    # 8.8 ------------------------------------------------------------------
    @freeze_time("2026-04-05 10:00:00")
    def test_8_8_signed_token_valid_for_correct_ticket_only(self):
        """8.8 Signed survey token is valid for the correct ticket only."""
        ticket_a = self.create_ticket(
            self.tenant_a, self.admin_a,
            contact=self.contact_a,
            status=self.status_resolved_a,
        )
        ticket_b = self.create_ticket(
            self.tenant_a, self.admin_a,
            contact=self.contact_a,
            status=self.status_resolved_a,
        )

        # Generate token for ticket_a
        token_a = self._generate_csat_token(ticket_a)

        # Submit for ticket_a — should succeed
        resp = self.client.post(
            "/api/v1/tickets/csat/",
            {"token": token_a, "rating": 3},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)

        ticket_a.refresh_from_db()
        self.assertEqual(ticket_a.csat_rating, 3)

        # ticket_b should remain unaffected
        ticket_b.refresh_from_db()
        self.assertIsNone(ticket_b.csat_rating)

    # 8.9 ------------------------------------------------------------------
    @freeze_time("2026-04-05 10:00:00")
    def test_8_9_expired_or_tampered_token_rejected(self):
        """8.9 Expired or tampered token rejected."""
        ticket = self.create_ticket(
            self.tenant_a, self.admin_a,
            contact=self.contact_a,
            status=self.status_resolved_a,
        )

        # Tampered token
        valid_token = self._generate_csat_token(ticket)
        tampered_token = valid_token[:-5] + "XXXXX"

        resp_tampered = self.client.post(
            "/api/v1/tickets/csat/",
            {"token": tampered_token, "rating": 4},
            format="json",
        )
        self.assertEqual(resp_tampered.status_code, 400)
        self.assertIn("invalid", resp_tampered.data["detail"].lower())

        # Expired token (max_age = 12 days in the view)
        with freeze_time("2026-04-20 10:00:00"):
            resp_expired = self.client.post(
                "/api/v1/tickets/csat/",
                {"token": valid_token, "rating": 4},
                format="json",
            )
            self.assertEqual(resp_expired.status_code, 400)

        # Verify nothing saved
        ticket.refresh_from_db()
        self.assertIsNone(ticket.csat_rating)
