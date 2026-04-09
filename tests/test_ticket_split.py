"""
Tests for ticket splitting.

Validates that:
- Split moves specified comments to child
- Source ticket retains unselected comments
- Child ticket has SLA initialized
- Cannot split with comment_ids from a different ticket
- Cannot split with zero comment_ids
"""

import pytest
from django.contrib.contenttypes.models import ContentType

from conftest import (
    MembershipFactory,
    TicketFactory,
    TicketStatusFactory,
    UserFactory,
)
from main.context import clear_current_tenant, set_current_tenant

from apps.accounts.models import Role
from apps.comments.models import Comment
from apps.tickets.models import SLAPolicy, Ticket, TicketLink
from apps.tickets.services import split_ticket


def _setup(tenant, admin_user):
    """Create statuses, SLA policy, and return them."""
    set_current_tenant(tenant)
    open_status = TicketStatusFactory(
        tenant=tenant, name="Open", slug="open", is_default=True,
    )
    SLAPolicy.objects.create(
        tenant=tenant, name="Medium SLA", priority="medium",
        first_response_minutes=60, resolution_minutes=240,
        business_hours_only=False, is_active=True,
    )
    return open_status


def _make_ticket(tenant, status, user, **kwargs):
    """Create a ticket using model save (not factory) so TicketCounter stays in sync."""
    t = Ticket(
        subject=kwargs.get("subject", "Test ticket"),
        description="Test",
        status=status,
        created_by=user,
        tenant=tenant,
        priority=kwargs.get("priority", "medium"),
    )
    t.save()
    return t


def _make_comments(ticket, user, count=3):
    """Create comments on a ticket and return their PKs."""
    ct = ContentType.objects.get_for_model(Ticket)
    pks = []
    for i in range(count):
        c = Comment(
            content_type=ct,
            object_id=ticket.pk,
            author=user,
            body=f"Comment {i}",
            tenant=ticket.tenant,
        )
        c.save()
        pks.append(c.pk)
    return pks


class TestSplitMovesComments:
    """Split moves specified comments to child ticket."""

    def test_specified_comments_moved(self, tenant, admin_user):
        open_status = _setup(tenant, admin_user)
        source = _make_ticket(tenant, open_status, admin_user)

        comment_pks = _make_comments(source, admin_user, count=4)
        to_move = comment_pks[:2]  # move first two
        to_keep = comment_pks[2:]  # keep last two

        child = split_ticket(
            source=source,
            comment_ids=to_move,
            actor=admin_user,
            new_ticket_data={"subject": "Split child"},
        )
        clear_current_tenant()

        ct = ContentType.objects.get_for_model(Ticket)

        # Child should have the 2 moved comments + 1 system "Split from" comment
        child_comments = Comment.unscoped.filter(
            content_type=ct, object_id=child.pk,
        )
        assert child_comments.count() == 3  # 2 moved + 1 system
        moved_ids = set(child_comments.values_list("pk", flat=True))
        for pk in to_move:
            assert pk in moved_ids


class TestSourceRetainsComments:
    """Source ticket retains unselected comments."""

    def test_unselected_comments_stay(self, tenant, admin_user):
        open_status = _setup(tenant, admin_user)
        source = _make_ticket(tenant, open_status, admin_user)

        comment_pks = _make_comments(source, admin_user, count=4)
        to_move = comment_pks[:2]
        to_keep = comment_pks[2:]

        split_ticket(
            source=source,
            comment_ids=to_move,
            actor=admin_user,
            new_ticket_data={"subject": "Split child"},
        )
        clear_current_tenant()

        ct = ContentType.objects.get_for_model(Ticket)

        # Source should retain the 2 unselected + 1 system "Ticket split" comment
        source_comments = Comment.unscoped.filter(
            content_type=ct, object_id=source.pk,
        )
        assert source_comments.count() == 3  # 2 retained + 1 system
        retained_ids = set(source_comments.values_list("pk", flat=True))
        for pk in to_keep:
            assert pk in retained_ids


class TestChildHasSLA:
    """Child ticket has SLA initialized."""

    def test_sla_initialized_on_child(self, tenant, admin_user):
        open_status = _setup(tenant, admin_user)
        source = _make_ticket(tenant, open_status, admin_user, priority="medium")
        comment_pks = _make_comments(source, admin_user, count=1)

        child = split_ticket(
            source=source,
            comment_ids=comment_pks,
            actor=admin_user,
            new_ticket_data={"subject": "SLA child", "priority": "medium"},
        )
        clear_current_tenant()

        child.refresh_from_db()
        assert child.sla_policy is not None
        assert child.sla_first_response_due is not None
        assert child.sla_resolution_due is not None

    def test_related_to_link_created(self, tenant, admin_user):
        open_status = _setup(tenant, admin_user)
        source = _make_ticket(tenant, open_status, admin_user)
        comment_pks = _make_comments(source, admin_user, count=1)

        child = split_ticket(
            source=source,
            comment_ids=comment_pks,
            actor=admin_user,
            new_ticket_data={"subject": "Link check"},
        )
        clear_current_tenant()

        link = TicketLink.unscoped.filter(
            source_ticket=child,
            target_ticket=source,
            link_type=TicketLink.LinkType.RELATED_TO,
        )
        assert link.exists()


class TestSplitCrossTenantComments:
    """Cannot split with comment_ids from a different ticket."""

    def test_foreign_comment_raises(self, tenant, admin_user):
        open_status = _setup(tenant, admin_user)
        source = _make_ticket(tenant, open_status, admin_user)
        other_ticket = _make_ticket(tenant, open_status, admin_user)

        # Create comments on the OTHER ticket
        foreign_pks = _make_comments(other_ticket, admin_user, count=2)

        with pytest.raises(ValueError, match="do not belong"):
            split_ticket(
                source=source,
                comment_ids=foreign_pks,
                actor=admin_user,
                new_ticket_data={"subject": "Should fail"},
            )

        clear_current_tenant()


class TestSplitZeroComments:
    """Cannot split with zero comment_ids."""

    def test_empty_list_raises(self, tenant, admin_user):
        open_status = _setup(tenant, admin_user)
        source = _make_ticket(tenant, open_status, admin_user)

        with pytest.raises(ValueError, match="At least one comment"):
            split_ticket(
                source=source,
                comment_ids=[],
                actor=admin_user,
                new_ticket_data={"subject": "Should fail"},
            )

        clear_current_tenant()
