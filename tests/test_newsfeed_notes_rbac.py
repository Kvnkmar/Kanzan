"""Regression tests for newsfeed RBAC + draft-broadcast suppression and
per-user QuickNote isolation.

Covers:
  (a) Newsfeed RBAC — only Admin/Manager may create a NewsPost (agents/viewers
      get 403); any active member may list/read published posts.
  (b) Newsfeed signal — saving an UNPUBLISHED draft NewsPost must NOT emit a
      live broadcast (it fans to every connected tenant member and would leak
      the draft); a published post DOES emit one.
  (c) Notes — QuickNote is strictly per-user: user A can never list or retrieve
      user B's note via /api/v1/notes/.
"""

from unittest.mock import patch

import pytest

from apps.newsfeed.models import NewsPost
from apps.notes.models import QuickNote
from main.context import tenant_context

from conftest import MembershipFactory, make_api_client


NEWSFEED_URL = "/api/v1/newsfeed/posts/"
NOTES_URL = "/api/v1/notes/notes/"


# ── (a) Newsfeed RBAC ────────────────────────────────────────────────


@pytest.mark.django_db
class TestNewsfeedCreateRBAC:
    def _payload(self):
        return {
            "title": "Company All-Hands",
            "content": "Friday 3pm, main room.",
            "category": "announcement",
        }

    def test_admin_can_create_post(self, admin_client):
        resp = admin_client.post(NEWSFEED_URL, self._payload(), format="json")
        assert resp.status_code == 201, resp.content
        assert resp.data["title"] == "Company All-Hands"

    def test_manager_can_create_post(self, manager_client):
        resp = manager_client.post(NEWSFEED_URL, self._payload(), format="json")
        assert resp.status_code == 201, resp.content

    def test_agent_cannot_create_post(self, agent_client):
        resp = agent_client.post(NEWSFEED_URL, self._payload(), format="json")
        assert resp.status_code == 403, resp.content
        assert not NewsPost.unscoped.filter(title="Company All-Hands").exists()

    def test_viewer_cannot_create_post(self, viewer_client):
        resp = viewer_client.post(NEWSFEED_URL, self._payload(), format="json")
        assert resp.status_code == 403, resp.content

    def test_anonymous_cannot_create_post(self, anon_client):
        resp = anon_client.post(NEWSFEED_URL, self._payload(), format="json")
        assert resp.status_code in (401, 403), resp.content


@pytest.mark.django_db
class TestNewsfeedReadAccess:
    def _make_published_post(self, tenant, author):
        with tenant_context(tenant):
            return NewsPost.objects.create(
                tenant=tenant,
                author=author,
                title="Published News",
                content="Visible to all members.",
                category="general",
                is_published=True,
            )

    def test_agent_can_list_published_posts(self, tenant, agent_user, admin_user):
        self._make_published_post(tenant, admin_user)
        client = make_api_client(agent_user, tenant)
        resp = client.get(NEWSFEED_URL)
        assert resp.status_code == 200, resp.content
        titles = [p["title"] for p in resp.data["results"]]
        assert "Published News" in titles

    def test_viewer_can_list_published_posts(self, tenant, viewer_user, admin_user):
        self._make_published_post(tenant, admin_user)
        client = make_api_client(viewer_user, tenant)
        resp = client.get(NEWSFEED_URL)
        assert resp.status_code == 200, resp.content
        titles = [p["title"] for p in resp.data["results"]]
        assert "Published News" in titles

    def test_agent_can_retrieve_published_post(self, tenant, agent_user, admin_user):
        post = self._make_published_post(tenant, admin_user)
        client = make_api_client(agent_user, tenant)
        resp = client.get(f"{NEWSFEED_URL}{post.id}/")
        assert resp.status_code == 200, resp.content
        assert resp.data["title"] == "Published News"

    def test_agent_cannot_see_unpublished_draft_in_list(
        self, tenant, agent_user, admin_user
    ):
        with tenant_context(tenant):
            NewsPost.objects.create(
                tenant=tenant,
                author=admin_user,
                title="Secret Draft",
                content="Not ready yet.",
                category="general",
                is_published=False,
            )
        client = make_api_client(agent_user, tenant)
        resp = client.get(NEWSFEED_URL)
        assert resp.status_code == 200, resp.content
        titles = [p["title"] for p in resp.data["results"]]
        assert "Secret Draft" not in titles


# ── (a2) Newsfeed 24-hour auto-expiry ────────────────────────────────


@pytest.mark.django_db
class TestNewsfeedAutoExpiry:
    """New posts are available for 24h then auto-hide, unless an explicit
    expiry was supplied."""

    def _payload(self):
        return {
            "title": "Daily Standup Notes",
            "content": "Sprint board is green.",
            "category": "update",
        }

    def test_create_defaults_to_24h_expiry(self, admin_client):
        from django.utils import timezone

        before = timezone.now()
        resp = admin_client.post(NEWSFEED_URL, self._payload(), format="json")
        assert resp.status_code == 201, resp.content

        post = NewsPost.unscoped.get(id=resp.data["id"])
        assert post.expires_at is not None
        delta = post.expires_at - before
        # ~24h, allowing a little slack for test execution time.
        assert timezone.timedelta(hours=23, minutes=59) <= delta <= timezone.timedelta(hours=24, minutes=1)

    def test_explicit_expiry_is_respected(self, admin_client):
        from django.utils import timezone

        custom = timezone.now() + timezone.timedelta(days=7)
        payload = {**self._payload(), "expires_at": custom.isoformat()}
        resp = admin_client.post(NEWSFEED_URL, payload, format="json")
        assert resp.status_code == 201, resp.content

        post = NewsPost.unscoped.get(id=resp.data["id"])
        assert abs((post.expires_at - custom).total_seconds()) < 2

    def test_expired_post_hidden_from_list(self, tenant, admin_user, agent_user):
        from django.utils import timezone

        with tenant_context(tenant):
            NewsPost.objects.create(
                tenant=tenant,
                author=admin_user,
                title="Stale Post",
                content="Should be gone.",
                category="general",
                is_published=True,
                expires_at=timezone.now() - timezone.timedelta(minutes=1),
            )
        client = make_api_client(agent_user, tenant)
        resp = client.get(NEWSFEED_URL)
        assert resp.status_code == 200, resp.content
        titles = [p["title"] for p in resp.data["results"]]
        assert "Stale Post" not in titles


# ── (b) Newsfeed draft-broadcast suppression ─────────────────────────


@pytest.mark.django_db
class TestNewsfeedDraftBroadcastSuppression:
    """The post_save receiver must early-return for unpublished drafts so the
    tenant-wide live channel never leaks an unpublished title/category."""

    def test_unpublished_draft_does_not_broadcast(self, tenant, admin_user):
        with patch("apps.newsfeed.signals.broadcast_live_event") as mock_bcast:
            with tenant_context(tenant):
                NewsPost.objects.create(
                    tenant=tenant,
                    author=admin_user,
                    title="Draft Only",
                    content="hush",
                    category="general",
                    is_published=False,
                )
        mock_bcast.assert_not_called()

    def test_published_post_broadcasts_created(self, tenant, admin_user):
        with patch("apps.newsfeed.signals.broadcast_live_event") as mock_bcast:
            with tenant_context(tenant):
                NewsPost.objects.create(
                    tenant=tenant,
                    author=admin_user,
                    title="Live Now",
                    content="hello",
                    category="general",
                    is_published=True,
                )
        assert mock_bcast.called
        # Most-recent NewsPost save should have fired newsfeed.created.
        events = [c.kwargs.get("event") for c in mock_bcast.call_args_list]
        assert "newsfeed.created" in events

    def test_publishing_a_draft_then_broadcasts(self, tenant, admin_user):
        with tenant_context(tenant):
            post = NewsPost.objects.create(
                tenant=tenant,
                author=admin_user,
                title="Will Publish",
                content="soon",
                category="general",
                is_published=False,
            )
        with patch("apps.newsfeed.signals.broadcast_live_event") as mock_bcast:
            with tenant_context(tenant):
                post.is_published = True
                post.save()
        assert mock_bcast.called
        events = [c.kwargs.get("event") for c in mock_bcast.call_args_list]
        # It is an update (created=False) at this point.
        assert "newsfeed.updated" in events


# ── (c) QuickNote per-user isolation ─────────────────────────────────


@pytest.mark.django_db
class TestQuickNoteUserIsolation:
    def _make_note(self, tenant, user, content):
        with tenant_context(tenant):
            return QuickNote.objects.create(
                tenant=tenant, user=user, content=content
            )

    def test_user_cannot_list_other_users_note(self, tenant):
        user_a = MembershipFactory(tenant=tenant).user
        user_b = MembershipFactory(tenant=tenant).user
        self._make_note(tenant, user_b, "B's private note")

        client_a = make_api_client(user_a, tenant)
        resp = client_a.get(NOTES_URL)
        assert resp.status_code == 200, resp.content
        contents = [n["content"] for n in resp.data["results"]]
        assert "B's private note" not in contents

    def test_user_cannot_retrieve_other_users_note(self, tenant):
        user_a = MembershipFactory(tenant=tenant).user
        user_b = MembershipFactory(tenant=tenant).user
        note_b = self._make_note(tenant, user_b, "B's private note")

        client_a = make_api_client(user_a, tenant)
        resp = client_a.get(f"{NOTES_URL}{note_b.id}/")
        assert resp.status_code == 404, resp.content

    def test_user_sees_only_own_notes(self, tenant):
        user_a = MembershipFactory(tenant=tenant).user
        user_b = MembershipFactory(tenant=tenant).user
        self._make_note(tenant, user_a, "A's note")
        self._make_note(tenant, user_b, "B's note")

        client_a = make_api_client(user_a, tenant)
        resp = client_a.get(NOTES_URL)
        assert resp.status_code == 200, resp.content
        contents = [n["content"] for n in resp.data["results"]]
        assert contents == ["A's note"]

    def test_create_assigns_note_to_requesting_user(self, tenant):
        user_a = MembershipFactory(tenant=tenant).user
        client_a = make_api_client(user_a, tenant)
        resp = client_a.post(
            NOTES_URL, {"content": "mine", "color": "blue"}, format="json"
        )
        assert resp.status_code == 201, resp.content
        with tenant_context(tenant):
            note = QuickNote.objects.get(id=resp.data["id"])
        assert note.user_id == user_a.id
