"""
Tests for ticket linking and merging.

Validates that:
- merge_tickets moves all comments to primary
- secondary is closed after merge
- Cannot merge tickets from different tenants
- Cannot merge a ticket into itself
- Linking creates a TicketLink record
- Linking is bidirectional in display (GET returns links in both directions)
"""

import pytest
from django.contrib.contenttypes.models import ContentType

from conftest import (
    MembershipFactory,
    TenantFactory,
    TicketFactory,
    TicketStatusFactory,
    UserFactory,
    make_api_client,
)
from main.context import clear_current_tenant, set_current_tenant

from apps.accounts.models import Role
from apps.comments.models import ActivityLog, Comment
from apps.tickets.models import Ticket, TicketActivity, TicketLink
from apps.tickets.services import merge_tickets


def _setup(tenant, admin_user):
    """Create statuses and return them."""
    set_current_tenant(tenant)
    open_status = TicketStatusFactory(
        tenant=tenant, name="Open", slug="open", is_default=True,
    )
    closed_status = TicketStatusFactory(
        tenant=tenant, name="Closed", slug="closed", is_closed=True,
    )
    return open_status, closed_status


class TestMergeMovesComments:
    """merge_tickets moves all comments from secondary to primary."""

    def test_comments_transferred(self, tenant, admin_user):
        open_status, _ = _setup(tenant, admin_user)

        primary = TicketFactory(
            tenant=tenant, status=open_status, created_by=admin_user,
        )
        secondary = TicketFactory(
            tenant=tenant, status=open_status, created_by=admin_user,
        )

        # Create comments on the secondary ticket
        ticket_ct = ContentType.objects.get_for_model(Ticket)
        for i in range(3):
            Comment(
                content_type=ticket_ct,
                object_id=secondary.pk,
                author=admin_user,
                body=f"Comment {i} on secondary",
                tenant=tenant,
            ).save()

        # Create one comment on the primary
        Comment(
            content_type=ticket_ct,
            object_id=primary.pk,
            author=admin_user,
            body="Original comment on primary",
            tenant=tenant,
        ).save()

        merge_tickets(primary, secondary, admin_user)
        clear_current_tenant()

        # All 3 secondary comments should now be on the primary
        # (plus the original 1 = 4 comments on primary, plus 1 system merge comment on secondary)
        primary_comments = Comment.unscoped.filter(
            content_type=ticket_ct, object_id=primary.pk,
        ).count()
        assert primary_comments == 4  # 1 original + 3 transferred

        # Secondary should have only the system merge comment
        secondary_comments = Comment.unscoped.filter(
            content_type=ticket_ct, object_id=secondary.pk,
        ).count()
        assert secondary_comments == 1
        merge_comment = Comment.unscoped.filter(
            content_type=ticket_ct, object_id=secondary.pk,
        ).first()
        assert "Merged into" in merge_comment.body


class TestMergeClosesSecondary:
    """Secondary ticket is closed after merge."""

    def test_secondary_closed(self, tenant, admin_user):
        open_status, _ = _setup(tenant, admin_user)

        primary = TicketFactory(
            tenant=tenant, status=open_status, created_by=admin_user,
        )
        secondary = TicketFactory(
            tenant=tenant, status=open_status, created_by=admin_user,
        )

        merge_tickets(primary, secondary, admin_user)
        clear_current_tenant()

        secondary.refresh_from_db()
        assert secondary.status.is_closed is True
        assert secondary.merged_into_id == primary.pk
        assert secondary.closed_at is not None

    def test_duplicate_of_link_created(self, tenant, admin_user):
        open_status, _ = _setup(tenant, admin_user)

        primary = TicketFactory(
            tenant=tenant, status=open_status, created_by=admin_user,
        )
        secondary = TicketFactory(
            tenant=tenant, status=open_status, created_by=admin_user,
        )

        merge_tickets(primary, secondary, admin_user)
        clear_current_tenant()

        link = TicketLink.unscoped.filter(
            source_ticket=secondary,
            target_ticket=primary,
            link_type=TicketLink.LinkType.DUPLICATE_OF,
        )
        assert link.exists()

    def test_audit_log_on_primary(self, tenant, admin_user):
        open_status, _ = _setup(tenant, admin_user)

        primary = TicketFactory(
            tenant=tenant, status=open_status, created_by=admin_user,
        )
        secondary = TicketFactory(
            tenant=tenant, status=open_status, created_by=admin_user,
        )

        merge_tickets(primary, secondary, admin_user)
        clear_current_tenant()

        ticket_ct = ContentType.objects.get_for_model(Ticket)
        audit = ActivityLog.unscoped.filter(
            content_type=ticket_ct,
            object_id=primary.pk,
            action=ActivityLog.Action.UPDATED,
            description__icontains="merged",
        )
        assert audit.exists()


class TestMergeCrossTenantBlocked:
    """Cannot merge tickets from different tenants."""

    def test_cross_tenant_raises(self, tenant, admin_user):
        open_status, _ = _setup(tenant, admin_user)

        tenant_b = TenantFactory(slug="other-tenant")
        set_current_tenant(tenant_b)
        open_b = TicketStatusFactory(
            tenant=tenant_b, name="Open", slug="open", is_default=True,
        )
        user_b = UserFactory()
        MembershipFactory(
            user=user_b, tenant=tenant_b,
            role=Role.unscoped.get(tenant=tenant_b, slug="admin"),
        )

        set_current_tenant(tenant)
        ticket_a = TicketFactory(
            tenant=tenant, status=open_status, created_by=admin_user,
        )
        set_current_tenant(tenant_b)
        ticket_b = TicketFactory(
            tenant=tenant_b, status=open_b, created_by=user_b,
        )

        with pytest.raises(ValueError, match="different tenants"):
            merge_tickets(ticket_a, ticket_b, admin_user)

        clear_current_tenant()


class TestMergeSelfBlocked:
    """Cannot merge a ticket into itself."""

    def test_self_merge_raises(self, tenant, admin_user):
        open_status, _ = _setup(tenant, admin_user)

        ticket = TicketFactory(
            tenant=tenant, status=open_status, created_by=admin_user,
        )

        with pytest.raises(ValueError, match="itself"):
            merge_tickets(ticket, ticket, admin_user)

        clear_current_tenant()


class TestLinkingBidirectional:
    """Links stored as one record but returned for both directions."""

    def test_link_visible_from_both_tickets(self, tenant, admin_user):
        open_status, _ = _setup(tenant, admin_user)

        ticket_a = TicketFactory(
            tenant=tenant, status=open_status, created_by=admin_user,
        )
        ticket_b = TicketFactory(
            tenant=tenant, status=open_status, created_by=admin_user,
        )

        TicketLink.objects.create(
            source_ticket=ticket_a,
            target_ticket=ticket_b,
            link_type=TicketLink.LinkType.RELATED_TO,
            created_by=admin_user,
            tenant=tenant,
        )

        # Query links for ticket_a — should find it
        from django.db.models import Q
        links_a = TicketLink.objects.filter(
            Q(source_ticket=ticket_a) | Q(target_ticket=ticket_a),
        )
        assert links_a.count() == 1

        # Query links for ticket_b — should also find the same link
        links_b = TicketLink.objects.filter(
            Q(source_ticket=ticket_b) | Q(target_ticket=ticket_b),
        )
        assert links_b.count() == 1

        # It's the same record
        assert links_a.first().pk == links_b.first().pk

        clear_current_tenant()

    def test_link_api_returns_for_both(self, tenant, admin_user, admin_client):
        open_status, _ = _setup(tenant, admin_user)

        ticket_a = TicketFactory(
            tenant=tenant, status=open_status, created_by=admin_user,
        )
        ticket_b = TicketFactory(
            tenant=tenant, status=open_status, created_by=admin_user,
        )

        clear_current_tenant()

        # Create link via API
        resp = admin_client.post(
            f"/api/v1/tickets/tickets/{ticket_a.pk}/links/",
            {"target": str(ticket_b.pk), "link_type": "related_to"},
        )
        assert resp.status_code == 201

        # Both tickets should list the link
        resp_a = admin_client.get(f"/api/v1/tickets/tickets/{ticket_a.pk}/links/")
        assert resp_a.status_code == 200
        assert len(resp_a.data) == 1

        resp_b = admin_client.get(f"/api/v1/tickets/tickets/{ticket_b.pk}/links/")
        assert resp_b.status_code == 200
        assert len(resp_b.data) == 1
