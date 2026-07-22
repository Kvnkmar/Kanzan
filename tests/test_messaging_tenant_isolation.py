"""Regression tests for messaging cross-tenant isolation (messaging-1/2/3).

Before these fixes, mention resolution and DM/group conversation creation
queried the GLOBAL User table with no tenant-membership check, allowing a
user to mention / DM / pull-into-a-group users from OTHER tenants (account
enumeration + notification leakage). These tests pin the per-tenant scoping.
"""

from types import SimpleNamespace

import pytest

from apps.messaging.mentions import notify_mentions
from apps.messaging.models import Conversation, ConversationType, Message
from apps.messaging.serializers import ConversationCreateSerializer
from apps.notifications.models import Notification
from main.context import tenant_context

from conftest import MembershipFactory, TicketFactory, UserFactory


def _ctx(tenant, user):
    return {"request": SimpleNamespace(tenant=tenant, user=user)}


@pytest.mark.django_db
class TestConversationTenantScoping:
    def test_dm_with_cross_tenant_user_is_rejected(self, tenant, tenant_b, admin_user):
        foreign = MembershipFactory(tenant=tenant_b).user  # member of B only
        s = ConversationCreateSerializer(
            data={"type": ConversationType.DIRECT.value, "user_id": str(foreign.id)},
            context=_ctx(tenant, admin_user),
        )
        assert not s.is_valid()
        assert "user_id" in s.errors

    def test_dm_with_same_tenant_member_is_allowed(self, tenant, admin_user):
        mate = MembershipFactory(tenant=tenant).user
        s = ConversationCreateSerializer(
            data={"type": ConversationType.DIRECT.value, "user_id": str(mate.id)},
            context=_ctx(tenant, admin_user),
        )
        assert s.is_valid(), s.errors

    def test_group_with_cross_tenant_participant_is_rejected(
        self, tenant, tenant_b, admin_user
    ):
        mate = MembershipFactory(tenant=tenant).user
        foreign = MembershipFactory(tenant=tenant_b).user
        s = ConversationCreateSerializer(
            data={
                "type": ConversationType.GROUP.value,
                "name": "Ops",
                "user_ids": [str(mate.id), str(foreign.id)],
            },
            context=_ctx(tenant, admin_user),
        )
        assert not s.is_valid()
        assert "user_ids" in s.errors

    def test_ticket_conversation_with_cross_tenant_participant_is_rejected(
        self, tenant, tenant_b, admin_user
    ):
        """The TICKET branch was missed by the Sprint-0 hardening: a member
        could inject a cross-tenant user as a ticket-conversation participant,
        leaking message previews to a non-member's notifications socket."""
        foreign = MembershipFactory(tenant=tenant_b).user
        with tenant_context(tenant):
            ticket = TicketFactory(tenant=tenant, created_by=admin_user)
        s = ConversationCreateSerializer(
            data={
                "type": ConversationType.TICKET.value,
                "ticket_id": str(ticket.id),
                "user_ids": [str(foreign.id)],
            },
            context=_ctx(tenant, admin_user),
        )
        assert not s.is_valid()
        assert "user_ids" in s.errors

    def test_ticket_conversation_drops_cross_tenant_participant_on_write(
        self, tenant, tenant_b, admin_user
    ):
        """Defence-in-depth: even if validation is bypassed, the write path
        only adds active tenant members (plus the creator)."""
        foreign = MembershipFactory(tenant=tenant_b).user
        mate = MembershipFactory(tenant=tenant).user
        with tenant_context(tenant):
            ticket = TicketFactory(tenant=tenant, created_by=admin_user)
            serializer = ConversationCreateSerializer(context=_ctx(tenant, admin_user))
            conv = serializer._create_ticket(
                {"ticket_id": ticket.id, "user_ids": [foreign.id, mate.id]},
                admin_user,
                tenant,
            )
            member_ids = set(
                conv.participant_details.values_list("user_id", flat=True)
            )
        assert foreign.id not in member_ids
        assert mate.id in member_ids
        assert admin_user.id in member_ids

    def test_group_conversation_drops_cross_tenant_participant_on_write(
        self, tenant, tenant_b, admin_user
    ):
        """The GROUP write path now mirrors TICKET's fail-closed re-filter, so
        even a bypassed validate() cannot seed a cross-tenant participant."""
        foreign = MembershipFactory(tenant=tenant_b).user
        mate = MembershipFactory(tenant=tenant).user
        with tenant_context(tenant):
            serializer = ConversationCreateSerializer(context=_ctx(tenant, admin_user))
            conv = serializer._create_group(
                {"name": "Ops", "user_ids": [foreign.id, mate.id]},
                admin_user,
                tenant,
            )
            member_ids = set(
                conv.participant_details.values_list("user_id", flat=True)
            )
        assert foreign.id not in member_ids
        assert mate.id in member_ids
        assert admin_user.id in member_ids

    def test_direct_conversation_write_path_rejects_non_member(
        self, tenant, tenant_b, admin_user
    ):
        """The _create_direct reuse/internal path independently rejects a
        cross-tenant other-party (fail-closed, 400 not 500)."""
        from rest_framework.exceptions import ValidationError

        foreign = MembershipFactory(tenant=tenant_b).user
        with tenant_context(tenant):
            serializer = ConversationCreateSerializer(context=_ctx(tenant, admin_user))
            with pytest.raises(ValidationError):
                serializer._create_direct(
                    {"user_id": foreign.id}, admin_user, tenant,
                )

    def test_group_refresh_drops_deactivated_member(self, tenant, admin_user):
        """The group_id existing-conversation refresh branch re-filters
        source_group.members to ACTIVE members — a member deactivated after
        joining the group is not re-added when the thread is re-opened."""
        from apps.accounts.models import UserGroup
        from apps.messaging.models import ConversationParticipant

        stale_membership = MembershipFactory(tenant=tenant)  # active initially
        stale = stale_membership.user
        with tenant_context(tenant):
            group = UserGroup.objects.create(name="Team X")
            group.members.set([admin_user, stale])
            conv = Conversation.objects.create(
                type=ConversationType.GROUP, name="Team X", source_group=group,
            )
            ConversationParticipant.objects.create(conversation=conv, user=admin_user)

            stale_membership.is_active = False
            stale_membership.save(update_fields=["is_active"])

            serializer = ConversationCreateSerializer(context=_ctx(tenant, admin_user))
            returned = serializer._create_group(
                {"group_id": group.id}, admin_user, tenant,
            )
            member_ids = set(
                returned.participant_details.values_list("user_id", flat=True)
            )
        assert returned.id == conv.id            # reused the existing thread
        assert stale.id not in member_ids        # deactivated member NOT re-added
        assert admin_user.id in member_ids


@pytest.mark.django_db
class TestMentionTenantScoping:
    def test_cross_tenant_mention_sends_no_notification(
        self, tenant, tenant_b, admin_user
    ):
        foreign = MembershipFactory(tenant=tenant_b).user
        with tenant_context(tenant):
            conv = Conversation.objects.create(type=ConversationType.GROUP, name="G")
            msg = Message.objects.create(
                conversation=conv,
                author=admin_user,
                body=f"hello @[Foreigner](user:{foreign.id})",
            )
        notify_mentions(msg, tenant)
        assert not Notification.unscoped.filter(recipient=foreign).exists()

    def test_same_tenant_mention_sends_notification(self, tenant, admin_user):
        mate = MembershipFactory(tenant=tenant).user
        with tenant_context(tenant):
            conv = Conversation.objects.create(type=ConversationType.GROUP, name="G")
            msg = Message.objects.create(
                conversation=conv,
                author=admin_user,
                body=f"hi @[Mate](user:{mate.id})",
            )
        notify_mentions(msg, tenant)
        assert Notification.unscoped.filter(recipient=mate).exists()
