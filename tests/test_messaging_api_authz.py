"""Regression tests for messaging DRF authorization.

Pins the access-control guarantees of ``ConversationViewSet`` and
``MessageViewSet`` (apps/messaging/views.py):

* non-participants cannot retrieve/post to a conversation; participants can;
* message edit / broadcast are author-only;
* ``add_participant`` rejects cross-tenant users AND denies when there is no
  tenant context (the guard was hardened to raise "Tenant context required.");
* ``search_participants`` returns only same-tenant members and [] with no tenant.

These exercise the HTTP layer via ``make_api_client``. The two no-tenant
branches cannot be reached through the test client (it always sets a tenant
subdomain host), so they are driven by calling the action directly with a
``request.tenant = None`` stand-in — this is exactly the code path the guard
protects.
"""

from types import SimpleNamespace

import pytest
from rest_framework.exceptions import ValidationError

from apps.messaging.models import (
    Conversation,
    ConversationParticipant,
    ConversationType,
    Message,
)
from apps.messaging.views import ConversationViewSet
from main.context import tenant_context

from conftest import MembershipFactory, make_api_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _group_with(tenant, *users, name="Ops"):
    """Create a GROUP conversation with the given users as participants."""
    with tenant_context(tenant):
        conv = Conversation.objects.create(type=ConversationType.GROUP, name=name)
        for u in users:
            ConversationParticipant.objects.create(conversation=conv, user=u)
    return conv


def _msg(conv, author, body="hello"):
    with tenant_context(conv.tenant):
        return Message.objects.create(conversation=conv, author=author, body=body)


# ---------------------------------------------------------------------------
# Conversation retrieve / message post participant gating
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestConversationParticipantAccess:
    def test_participant_can_retrieve(self, tenant, admin_user):
        conv = _group_with(tenant, admin_user)
        client = make_api_client(admin_user, tenant)
        resp = client.get(f"/api/v1/messaging/conversations/{conv.pk}/")
        assert resp.status_code == 200
        assert str(resp.data["id"]) == str(conv.pk)

    def test_non_participant_cannot_retrieve(self, tenant, admin_user):
        """A tenant member who is NOT a participant is filtered out (404)."""
        conv = _group_with(tenant, admin_user)
        outsider = MembershipFactory(tenant=tenant).user
        client = make_api_client(outsider, tenant)
        resp = client.get(f"/api/v1/messaging/conversations/{conv.pk}/")
        # get_queryset filters to participants only -> object not found.
        assert resp.status_code == 404

    def test_participant_can_post_message(self, tenant, admin_user):
        conv = _group_with(tenant, admin_user)
        client = make_api_client(admin_user, tenant)
        resp = client.post(
            f"/api/v1/messaging/conversations/{conv.pk}/messages/",
            {"body": "hi team"},
            format="json",
        )
        assert resp.status_code == 201, resp.data
        assert Message.unscoped.filter(conversation=conv).count() == 1

    def test_non_participant_cannot_post_message(self, tenant, admin_user):
        conv = _group_with(tenant, admin_user)
        outsider = MembershipFactory(tenant=tenant).user
        client = make_api_client(outsider, tenant)
        resp = client.post(
            f"/api/v1/messaging/conversations/{conv.pk}/messages/",
            {"body": "let me in"},
            format="json",
        )
        # _get_conversation raises PermissionDenied -> 403.
        assert resp.status_code == 403
        assert Message.unscoped.filter(conversation=conv).count() == 0

    def test_non_participant_cannot_list_messages(self, tenant, admin_user):
        conv = _group_with(tenant, admin_user)
        _msg(conv, admin_user, "secret")
        outsider = MembershipFactory(tenant=tenant).user
        client = make_api_client(outsider, tenant)
        resp = client.get(
            f"/api/v1/messaging/conversations/{conv.pk}/messages/"
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Message edit / broadcast are author-only
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMessageAuthorOnly:
    def test_author_can_edit_own_message(self, tenant, admin_user):
        mate = MembershipFactory(tenant=tenant).user
        conv = _group_with(tenant, admin_user, mate)
        msg = _msg(conv, admin_user, "v1")
        client = make_api_client(admin_user, tenant)
        resp = client.patch(
            f"/api/v1/messaging/conversations/{conv.pk}/messages/{msg.pk}/",
            {"body": "v2"},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        msg.refresh_from_db()
        # Correct behavior: an author's edit should persist the new body.
        assert msg.body == "v2"
        assert msg.is_edited is True

    def test_other_member_cannot_edit_message(self, tenant, admin_user):
        mate = MembershipFactory(tenant=tenant).user
        conv = _group_with(tenant, admin_user, mate)
        msg = _msg(conv, admin_user, "original")
        client = make_api_client(mate, tenant)
        resp = client.patch(
            f"/api/v1/messaging/conversations/{conv.pk}/messages/{msg.pk}/",
            {"body": "tampered"},
            format="json",
        )
        assert resp.status_code == 403
        msg.refresh_from_db()
        assert msg.body == "original"

    def test_other_member_cannot_delete_message(self, tenant, admin_user):
        mate = MembershipFactory(tenant=tenant).user
        conv = _group_with(tenant, admin_user, mate)
        msg = _msg(conv, admin_user, "keep me")
        client = make_api_client(mate, tenant)
        resp = client.delete(
            f"/api/v1/messaging/conversations/{conv.pk}/messages/{msg.pk}/"
        )
        assert resp.status_code == 403
        assert Message.unscoped.filter(pk=msg.pk).exists()

    def test_author_can_broadcast(self, tenant, admin_user):
        mate = MembershipFactory(tenant=tenant).user
        conv = _group_with(tenant, admin_user, mate)
        msg = _msg(conv, admin_user, "broadcast me")
        client = make_api_client(admin_user, tenant)
        resp = client.post(
            f"/api/v1/messaging/conversations/{conv.pk}/messages/{msg.pk}/broadcast/"
        )
        assert resp.status_code == 200, resp.data

    def test_non_author_cannot_broadcast(self, tenant, admin_user):
        mate = MembershipFactory(tenant=tenant).user
        conv = _group_with(tenant, admin_user, mate)
        msg = _msg(conv, admin_user, "mine only")
        client = make_api_client(mate, tenant)
        resp = client.post(
            f"/api/v1/messaging/conversations/{conv.pk}/messages/{msg.pk}/broadcast/"
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# add_participant: cross-tenant rejection + tenant-None denial
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAddParticipant:
    def test_add_cross_tenant_user_rejected(self, tenant, tenant_b, admin_user):
        conv = _group_with(tenant, admin_user)
        foreign = MembershipFactory(tenant=tenant_b).user  # member of B only
        client = make_api_client(admin_user, tenant)
        resp = client.post(
            f"/api/v1/messaging/conversations/{conv.pk}/add-participant/",
            {"user_id": str(foreign.id)},
            format="json",
        )
        assert resp.status_code == 400
        assert not conv.participant_details.filter(user=foreign).exists()

    def test_add_same_tenant_member_allowed(self, tenant, admin_user):
        conv = _group_with(tenant, admin_user)
        mate = MembershipFactory(tenant=tenant).user
        client = make_api_client(admin_user, tenant)
        resp = client.post(
            f"/api/v1/messaging/conversations/{conv.pk}/add-participant/",
            {"user_id": str(mate.id)},
            format="json",
        )
        assert resp.status_code == 201, resp.data
        assert conv.participant_details.filter(user=mate).exists()

    def test_add_participant_denied_when_no_tenant(self, tenant, admin_user):
        """The hardened guard must DENY (not skip) when request.tenant is None.

        The test client always resolves a tenant subdomain, so drive the
        action directly with a tenant-less request stand-in. The target user
        is a legitimate member, so a *missing* check would have admitted them.
        """
        conv = _group_with(tenant, admin_user)
        mate = MembershipFactory(tenant=tenant).user

        view = ConversationViewSet()
        view.kwargs = {"pk": str(conv.pk)}
        view.request = SimpleNamespace(
            tenant=None,
            user=admin_user,
            data={"user_id": str(mate.id)},
        )
        # get_object() would re-run tenant scoping; bypass it since we are
        # exercising the tenant-None guard, not object lookup.
        view.get_object = lambda: conv

        with pytest.raises(ValidationError) as exc:
            view.add_participant(view.request, pk=str(conv.pk))
        assert "Tenant context required." in str(exc.value)
        assert not conv.participant_details.filter(user=mate).exists()


# ---------------------------------------------------------------------------
# search_participants: same-tenant only + [] with no tenant
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSearchParticipants:
    def test_returns_only_same_tenant_members(self, tenant, tenant_b, admin_user):
        mate = MembershipFactory(tenant=tenant).user
        foreign = MembershipFactory(tenant=tenant_b).user
        client = make_api_client(admin_user, tenant)
        resp = client.get(
            "/api/v1/messaging/conversations/search-participants/"
        )
        assert resp.status_code == 200
        ids = {r["id"] for r in resp.data["results"]}
        assert str(mate.id) in ids
        assert str(foreign.id) not in ids
        # The requesting user is excluded from results.
        assert str(admin_user.id) not in ids

    def test_search_filter_matches_email(self, tenant, admin_user):
        mate = MembershipFactory(tenant=tenant).user
        client = make_api_client(admin_user, tenant)
        resp = client.get(
            "/api/v1/messaging/conversations/search-participants/",
            {"search": mate.email},
        )
        assert resp.status_code == 200
        ids = {r["id"] for r in resp.data["results"]}
        assert ids == {str(mate.id)}

    def test_returns_empty_list_when_no_tenant(self, tenant, admin_user):
        """With no tenant context the action returns an empty list, not a leak."""
        view = ConversationViewSet()
        view.request = SimpleNamespace(
            tenant=None,
            user=admin_user,
            query_params={},
        )
        resp = view.search_participants(view.request)
        assert resp.status_code == 200
        assert resp.data == []
