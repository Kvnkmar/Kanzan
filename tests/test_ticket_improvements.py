"""
Tests for the ticketing system improvements:
- Soft delete (is_deleted, restore)
- Ticket watchers (follow/unfollow)
- Time tracking (CRUD, summary)
- Ticket templates (CRUD, use/increment)
- Webhooks (CRUD, test delivery)
- Circular link prevention
- SLA breach prediction filters
- New ticket list filters (watching, sla_approaching, has_sla)
"""

import datetime

import pytest
from django.utils import timezone

from conftest import (
    MembershipFactory,
    QueueFactory,
    TicketFactory,
    TicketStatusFactory,
    UserFactory,
    make_api_client,
)
from main.context import clear_current_tenant, set_current_tenant


# ── Helpers ─────────────────────────────────────────────────────────────


@pytest.fixture
def ticket(tenant, default_status, admin_user):
    set_current_tenant(tenant)
    t = TicketFactory(tenant=tenant, status=default_status, created_by=admin_user)
    clear_current_tenant()
    return t


@pytest.fixture
def in_progress_status(tenant):
    set_current_tenant(tenant)
    s = TicketStatusFactory(
        tenant=tenant, name="In Progress", slug="in-progress", order=2,
    )
    clear_current_tenant()
    return s


# ── Soft Delete ─────────────────────────────────────────────────────────


class TestSoftDelete:
    def test_delete_ticket_is_soft(self, admin_client, ticket, tenant):
        """DELETE /tickets/{id}/ should soft-delete, not hard-delete."""
        url = f"/api/v1/tickets/tickets/{ticket.pk}/"
        resp = admin_client.delete(url)
        assert resp.status_code == 204

        # Ticket still exists in DB
        from apps.tickets.models import Ticket
        ticket.refresh_from_db()
        assert ticket.is_deleted is True
        assert ticket.deleted_at is not None

    def test_soft_deleted_hidden_from_list(self, admin_client, ticket, tenant):
        """Soft-deleted tickets should not appear in default list."""
        url = f"/api/v1/tickets/tickets/{ticket.pk}/"
        admin_client.delete(url)

        resp = admin_client.get("/api/v1/tickets/tickets/")
        assert resp.status_code == 200
        ids = [t["id"] for t in resp.data["results"]]
        assert str(ticket.pk) not in ids

    def test_soft_deleted_visible_with_include_deleted(self, admin_client, ticket, tenant):
        """?include_deleted=true should show soft-deleted tickets."""
        admin_client.delete(f"/api/v1/tickets/tickets/{ticket.pk}/")

        resp = admin_client.get("/api/v1/tickets/tickets/?include_deleted=true")
        assert resp.status_code == 200
        ids = [t["id"] for t in resp.data["results"]]
        assert str(ticket.pk) in ids

    def test_restore_ticket(self, admin_client, ticket, tenant):
        """POST /tickets/{id}/restore/ should restore soft-deleted ticket."""
        admin_client.delete(f"/api/v1/tickets/tickets/{ticket.pk}/")

        resp = admin_client.post(
            f"/api/v1/tickets/tickets/{ticket.pk}/restore/",
        )
        assert resp.status_code == 200

        ticket.refresh_from_db()
        assert ticket.is_deleted is False
        assert ticket.deleted_at is None

    def test_restore_non_deleted_ticket(self, admin_client, ticket, tenant):
        """Restoring a non-deleted ticket should return 400."""
        resp = admin_client.post(
            f"/api/v1/tickets/tickets/{ticket.pk}/restore/",
        )
        assert resp.status_code == 400


# ── Watchers ────────────────────────────────────────────────────────────


class TestTicketWatchers:
    def test_watch_toggle(self, admin_client, ticket, tenant):
        """POST /tickets/{id}/watch/ should toggle watch status."""
        url = f"/api/v1/tickets/tickets/{ticket.pk}/watch/"

        # Watch
        resp = admin_client.post(url)
        assert resp.status_code == 200
        assert resp.data["watching"] is True

        # Unwatch
        resp = admin_client.post(url)
        assert resp.status_code == 200
        assert resp.data["watching"] is False

    def test_add_watcher(self, admin_client, agent_user, ticket, tenant):
        """POST /tickets/{id}/watchers/ should add a watcher."""
        url = f"/api/v1/tickets/tickets/{ticket.pk}/watchers/"
        resp = admin_client.post(url, {"user": str(agent_user.pk)})
        assert resp.status_code == 201
        assert str(resp.data["user"]) == str(agent_user.pk)

    def test_add_duplicate_watcher(self, admin_client, agent_user, ticket, tenant):
        """Adding the same watcher twice should return 409."""
        url = f"/api/v1/tickets/tickets/{ticket.pk}/watchers/"
        admin_client.post(url, {"user": str(agent_user.pk)})
        resp = admin_client.post(url, {"user": str(agent_user.pk)})
        assert resp.status_code == 409

    def test_list_watchers(self, admin_client, agent_user, ticket, tenant):
        """GET /tickets/{id}/watchers/ should list watchers."""
        url = f"/api/v1/tickets/tickets/{ticket.pk}/watchers/"
        admin_client.post(url, {"user": str(agent_user.pk)})

        resp = admin_client.get(url)
        assert resp.status_code == 200
        assert len(resp.data) == 1

    def test_remove_watcher(self, admin_client, agent_user, ticket, tenant):
        """DELETE /tickets/{id}/watchers/{user_id}/ should remove."""
        url = f"/api/v1/tickets/tickets/{ticket.pk}/watchers/"
        admin_client.post(url, {"user": str(agent_user.pk)})

        resp = admin_client.delete(
            f"/api/v1/tickets/tickets/{ticket.pk}/watchers/{agent_user.pk}/",
        )
        assert resp.status_code == 204

    def test_watcher_count_in_list(self, admin_client, agent_user, ticket, tenant):
        """Ticket list should include watcher_count."""
        admin_client.post(
            f"/api/v1/tickets/tickets/{ticket.pk}/watchers/",
            {"user": str(agent_user.pk)},
        )

        resp = admin_client.get("/api/v1/tickets/tickets/")
        assert resp.status_code == 200
        ticket_data = next(
            t for t in resp.data["results"] if t["id"] == str(ticket.pk)
        )
        assert ticket_data["watcher_count"] == 1


# ── Time Tracking ───────────────────────────────────────────────────────


class TestTimeTracking:
    def test_log_time(self, admin_client, ticket, tenant):
        """POST /tickets/{id}/time-entries/ should log time."""
        url = f"/api/v1/tickets/tickets/{ticket.pk}/time-entries/"
        resp = admin_client.post(url, {
            "duration_minutes": 30,
            "description": "Investigated the issue",
            "is_billable": True,
        })
        assert resp.status_code == 201
        assert resp.data["duration_minutes"] == 30
        assert resp.data["is_billable"] is True

    def test_list_time_entries(self, admin_client, ticket, tenant):
        """GET /tickets/{id}/time-entries/ should list entries."""
        url = f"/api/v1/tickets/tickets/{ticket.pk}/time-entries/"
        admin_client.post(url, {"duration_minutes": 15, "description": "Call"})
        admin_client.post(url, {"duration_minutes": 45, "description": "Fix"})

        resp = admin_client.get(url)
        assert resp.status_code == 200
        # Paginated
        results = resp.data.get("results", resp.data)
        assert len(results) == 2

    def test_time_summary(self, admin_client, ticket, tenant):
        """GET /tickets/{id}/time-summary/ should return aggregated data."""
        url = f"/api/v1/tickets/tickets/{ticket.pk}/time-entries/"
        admin_client.post(url, {"duration_minutes": 15, "is_billable": True})
        admin_client.post(url, {"duration_minutes": 45, "is_billable": False})

        resp = admin_client.get(
            f"/api/v1/tickets/tickets/{ticket.pk}/time-summary/",
        )
        assert resp.status_code == 200
        assert resp.data["total_minutes"] == 60
        assert resp.data["billable_minutes"] == 15
        assert resp.data["entry_count"] == 2
        assert len(resp.data["by_user"]) == 1

    def test_invalid_duration(self, admin_client, ticket, tenant):
        """Duration must be positive and <= 1440."""
        url = f"/api/v1/tickets/tickets/{ticket.pk}/time-entries/"
        resp = admin_client.post(url, {"duration_minutes": 0})
        assert resp.status_code == 400

        resp = admin_client.post(url, {"duration_minutes": 2000})
        assert resp.status_code == 400

    def test_delete_own_time_entry(self, admin_client, ticket, tenant):
        """Users can delete their own time entries."""
        url = f"/api/v1/tickets/tickets/{ticket.pk}/time-entries/"
        resp = admin_client.post(url, {"duration_minutes": 30})
        entry_id = resp.data["id"]

        resp = admin_client.delete(
            f"/api/v1/tickets/tickets/{ticket.pk}/time-entries/{entry_id}/",
        )
        assert resp.status_code == 204

    def test_total_time_in_list(self, admin_client, ticket, tenant):
        """Ticket list should include total_time_minutes."""
        admin_client.post(
            f"/api/v1/tickets/tickets/{ticket.pk}/time-entries/",
            {"duration_minutes": 30},
        )

        resp = admin_client.get("/api/v1/tickets/tickets/")
        ticket_data = next(
            t for t in resp.data["results"] if t["id"] == str(ticket.pk)
        )
        assert ticket_data["total_time_minutes"] == 30


# ── Ticket Templates ───────────────────────────────────────────────────


class TestTicketTemplates:
    def test_create_template(self, admin_client, tenant):
        """POST /ticket-templates/ should create a template."""
        resp = admin_client.post("/api/v1/tickets/ticket-templates/", {
            "name": "Bug Report",
            "description": "Template for bug reports",
            "subject_template": "[Bug] ",
            "body_template": "Steps to reproduce:\n1. \n\nExpected:\n\nActual:",
            "default_priority": "high",
        })
        assert resp.status_code == 201
        assert resp.data["name"] == "Bug Report"
        assert resp.data["usage_count"] == 0

    def test_list_templates(self, admin_client, admin_user, tenant):
        """GET /ticket-templates/ should list active templates."""
        # Create template directly in DB to avoid any API serialization issues
        set_current_tenant(tenant)
        from apps.tickets.models import TicketTemplate
        TicketTemplate.objects.create(
            name="Template A",
            tenant=tenant,
            created_by=admin_user,
        )
        clear_current_tenant()

        resp = admin_client.get("/api/v1/tickets/ticket-templates/")
        assert resp.status_code == 200
        data = resp.data
        results = data.get("results", data) if isinstance(data, dict) else data
        assert len(results) >= 1
        names = [t["name"] for t in results]
        assert "Template A" in names

    def test_use_template(self, admin_client, admin_user, tenant):
        """POST /ticket-templates/{id}/use/ should increment usage_count."""
        set_current_tenant(tenant)
        from apps.tickets.models import TicketTemplate
        template = TicketTemplate.objects.create(
            name="Quick Issue", tenant=tenant, created_by=admin_user,
        )
        clear_current_tenant()

        resp = admin_client.post(
            f"/api/v1/tickets/ticket-templates/{template.pk}/use/",
        )
        assert resp.status_code == 200
        assert resp.data["usage_count"] == 1


# ── Webhooks ────────────────────────────────────────────────────────────


class TestWebhooks:
    def test_create_webhook(self, admin_client, tenant):
        """POST /webhooks/ should create a webhook."""
        resp = admin_client.post("/api/v1/tickets/webhooks/", {
            "name": "Slack Notification",
            "url": "https://hooks.slack.example.com/test",
            "events": ["ticket.created", "ticket.closed"],
            "secret": "my-secret",
        }, format="json")
        assert resp.status_code == 201
        assert resp.data["name"] == "Slack Notification"
        # Secret should be write-only
        assert "secret" not in resp.data

    def test_invalid_event_type(self, admin_client, tenant):
        """Invalid event types should be rejected."""
        resp = admin_client.post("/api/v1/tickets/webhooks/", {
            "name": "Bad",
            "url": "https://example.com",
            "events": ["invalid.event"],
        }, format="json")
        assert resp.status_code == 400

    def test_empty_events(self, admin_client, tenant):
        """Empty events list should be rejected."""
        resp = admin_client.post("/api/v1/tickets/webhooks/", {
            "name": "Bad",
            "url": "https://example.com",
            "events": [],
        }, format="json")
        assert resp.status_code == 400

    def test_reset_failures(self, admin_client, tenant):
        """POST /webhooks/{id}/reset-failures/ should reset."""
        create_resp = admin_client.post("/api/v1/tickets/webhooks/", {
            "name": "Test",
            "url": "https://example.com",
            "events": ["ticket.created"],
        }, format="json")
        webhook_id = create_resp.data["id"]

        resp = admin_client.post(
            f"/api/v1/tickets/webhooks/{webhook_id}/reset-failures/",
        )
        assert resp.status_code == 200
        assert resp.data["failure_count"] == 0


# ── Circular Link Prevention ───────────────────────────────────────────


class TestCircularLinks:
    def test_self_link_prevented(self, admin_client, ticket, tenant):
        """Cannot link a ticket to itself."""
        resp = admin_client.post(
            f"/api/v1/tickets/tickets/{ticket.pk}/links/",
            {"target": str(ticket.pk), "link_type": "related_to"},
        )
        assert resp.status_code == 400

    def test_circular_blocks_prevented(self, admin_client, tenant, default_status, admin_user):
        """Creating A blocks B blocks A should be prevented."""
        set_current_tenant(tenant)
        ticket_a = TicketFactory(
            tenant=tenant, status=default_status, created_by=admin_user,
        )
        ticket_b = TicketFactory(
            tenant=tenant, status=default_status, created_by=admin_user,
        )
        clear_current_tenant()

        # A blocks B
        resp = admin_client.post(
            f"/api/v1/tickets/tickets/{ticket_a.pk}/links/",
            {"target": str(ticket_b.pk), "link_type": "blocks"},
        )
        assert resp.status_code == 201

        # B blocks A should fail (circular)
        from apps.tickets.models import TicketLink
        link = TicketLink(
            source_ticket=ticket_b,
            target_ticket=ticket_a,
            link_type=TicketLink.LinkType.BLOCKS,
            tenant=tenant,
        )
        assert link._creates_circular_dependency() is True


# ── SLA Filters ────────────────────────────────────────────────────────


class TestSLAFilters:
    def test_sla_approaching_filter(self, admin_client, tenant, default_status, admin_user):
        """?sla_approaching=true should filter approaching breach tickets."""
        set_current_tenant(tenant)
        now = timezone.now()

        # Ticket approaching response breach (due in 20 minutes)
        ticket = TicketFactory(
            tenant=tenant, status=default_status, created_by=admin_user,
        )
        from apps.tickets.models import Ticket
        Ticket.objects.filter(pk=ticket.pk).update(
            sla_first_response_due=now + datetime.timedelta(minutes=20),
            sla_response_breached=False,
        )

        clear_current_tenant()

        resp = admin_client.get("/api/v1/tickets/tickets/?sla_approaching=true")
        assert resp.status_code == 200
        ids = [t["id"] for t in resp.data["results"]]
        assert str(ticket.pk) in ids

    def test_has_sla_filter(self, admin_client, tenant, default_status, admin_user):
        """?has_sla=true should return only tickets with SLA policy."""
        set_current_tenant(tenant)
        ticket_with_sla = TicketFactory(
            tenant=tenant, status=default_status, created_by=admin_user,
        )
        ticket_no_sla = TicketFactory(
            tenant=tenant, status=default_status, created_by=admin_user,
        )

        from apps.tickets.models import SLAPolicy, Ticket
        policy = SLAPolicy.objects.create(
            tenant=tenant, name="Urgent", priority="urgent",
            first_response_minutes=60, resolution_minutes=480,
        )
        Ticket.objects.filter(pk=ticket_with_sla.pk).update(sla_policy=policy)
        clear_current_tenant()

        resp = admin_client.get("/api/v1/tickets/tickets/?has_sla=true")
        assert resp.status_code == 200
        ids = [t["id"] for t in resp.data["results"]]
        assert str(ticket_with_sla.pk) in ids
        assert str(ticket_no_sla.pk) not in ids

    def test_sla_remaining_in_list(self, admin_client, tenant, default_status, admin_user):
        """Ticket list should include SLA remaining minutes."""
        set_current_tenant(tenant)
        now = timezone.now()
        ticket = TicketFactory(
            tenant=tenant, status=default_status, created_by=admin_user,
        )
        from apps.tickets.models import Ticket
        Ticket.objects.filter(pk=ticket.pk).update(
            sla_first_response_due=now + datetime.timedelta(minutes=45),
            sla_resolution_due=now + datetime.timedelta(hours=4),
        )
        clear_current_tenant()

        resp = admin_client.get("/api/v1/tickets/tickets/")
        assert resp.status_code == 200
        ticket_data = next(
            t for t in resp.data["results"] if t["id"] == str(ticket.pk)
        )
        assert ticket_data["sla_response_remaining_minutes"] is not None
        assert ticket_data["sla_response_remaining_minutes"] > 0
