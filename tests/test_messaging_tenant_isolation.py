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

from conftest import MembershipFactory, UserFactory


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
