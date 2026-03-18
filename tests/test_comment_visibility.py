"""
Tests for comment is_internal visibility enforcement.

Verifies that internal comments (agent-only notes) are hidden from
non-admin/manager users via the TicketViewSet comments endpoint.
"""

from django.contrib.contenttypes.models import ContentType
from rest_framework.test import APIClient

from apps.comments.models import Comment
from apps.tickets.models import Ticket

from tests.base import TenantTestCase


class CommentInternalVisibilityTest(TenantTestCase):
    """Internal comments must be hidden from agents and viewers."""

    def setUp(self):
        super().setUp()
        self.set_tenant(self.tenant_a)
        self.ticket = self.make_ticket(
            self.tenant_a, self.admin_a, assignee=self.agent_a,
        )
        self.ct = ContentType.objects.get_for_model(Ticket)

        # Create a public comment and an internal note
        self.public_comment = Comment(
            content_type=self.ct,
            object_id=self.ticket.pk,
            author=self.admin_a,
            body="This is a public reply to the customer.",
            is_internal=False,
        )
        self.public_comment.save()

        self.internal_note = Comment(
            content_type=self.ct,
            object_id=self.ticket.pk,
            author=self.admin_a,
            body="INTERNAL: Do not share this with the customer.",
            is_internal=True,
        )
        self.internal_note.save()

    def _get_comments(self, user):
        """Hit the ticket comments endpoint as the given user."""
        client = APIClient()
        client.force_authenticate(user=user)
        url = f"/api/v1/tickets/tickets/{self.ticket.pk}/comments/"
        response = client.get(url, HTTP_HOST="tenant-a.localhost")
        return response

    def test_admin_sees_internal_comments(self):
        """Admin (hierarchy_level=10) can see internal notes."""
        response = self._get_comments(self.admin_a)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        # Handle paginated and non-paginated responses
        comments = data.get("results", data) if isinstance(data, dict) else data
        comment_ids = [c["id"] for c in comments]
        self.assertIn(str(self.public_comment.pk), comment_ids)
        self.assertIn(str(self.internal_note.pk), comment_ids)

    def test_agent_cannot_see_internal_comments(self):
        """Agent (hierarchy_level=30) cannot see internal notes."""
        response = self._get_comments(self.agent_a)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        comments = data.get("results", data) if isinstance(data, dict) else data
        comment_ids = [c["id"] for c in comments]
        self.assertIn(str(self.public_comment.pk), comment_ids)
        self.assertNotIn(str(self.internal_note.pk), comment_ids)

    def test_viewer_cannot_see_internal_comments(self):
        """Viewer (hierarchy_level=40) cannot see internal notes."""
        # Make the viewer the ticket creator so they can access it
        self.ticket.created_by = self.viewer_a
        self.ticket.save(update_fields=["created_by"])

        response = self._get_comments(self.viewer_a)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        comments = data.get("results", data) if isinstance(data, dict) else data
        comment_ids = [c["id"] for c in comments]
        self.assertNotIn(str(self.internal_note.pk), comment_ids)
