"""Regression tests for the 2026-07-13 QA-audit High-severity authorization fixes.

Each fix is covered by a DENY-and-ALLOW pair (the audit found floors that
silently *allowed*, so a test must assert the negative as well as the positive):

- KZ-H04  UserViewSet writes: an Agent cannot edit/deactivate another member;
          is_active is read-only; a member may edit only their own record;
          Manager+ may edit anyone.
- KZ-H01  Export creation is Manager+ only; ExportJob list is scoped to owner.
- KZ-H02  serve_protected_media exports/ authz: requester/Manager only.
- KZ-H03  Attachment object-level media authz + list scoping by ticket-visibility.
- KZ-H05  allauth local signup is disabled (no active-unverified accounts).
- KZ-H06  CallEventConsumer rejects non-members (no cross-tenant call metadata).

See docs/qa-audit-2026-07-13/ for the full audit.
"""

import pytest
from django.contrib.contenttypes.models import ContentType

from conftest import (
    MembershipFactory,
    TicketFactory,
    TicketStatusFactory,
    UserFactory,
    make_api_client,
)
from main.context import clear_current_tenant, set_current_tenant


# ---------------------------------------------------------------------------
# KZ-H04 — UserViewSet write authorization
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestUserViewSetWriteAuthz:
    def _url(self, user_id):
        return f"/api/v1/accounts/users/{user_id}/"

    def test_agent_cannot_patch_another_member(self, agent_client, admin_user):
        resp = agent_client.patch(
            self._url(admin_user.id), {"first_name": "Hacked"}, format="json"
        )
        assert resp.status_code == 403
        admin_user.refresh_from_db()
        assert admin_user.first_name != "Hacked"

    def test_agent_cannot_deactivate_admin(self, agent_client, admin_user):
        resp = agent_client.patch(
            self._url(admin_user.id), {"is_active": False}, format="json"
        )
        assert resp.status_code == 403
        admin_user.refresh_from_db()
        assert admin_user.is_active is True

    def test_agent_can_edit_own_profile(self, agent_client, agent_user):
        resp = agent_client.patch(
            self._url(agent_user.id), {"first_name": "Newname"}, format="json"
        )
        assert resp.status_code == 200
        agent_user.refresh_from_db()
        assert agent_user.first_name == "Newname"

    def test_is_active_read_only_even_for_self(self, agent_client, agent_user):
        # A member editing their own record still cannot flip is_active.
        resp = agent_client.patch(
            self._url(agent_user.id), {"is_active": False}, format="json"
        )
        assert resp.status_code == 200
        agent_user.refresh_from_db()
        assert agent_user.is_active is True

    def test_manager_can_patch_other_member(self, manager_client, agent_user):
        resp = manager_client.patch(
            self._url(agent_user.id), {"first_name": "Renamed"}, format="json"
        )
        assert resp.status_code == 200
        agent_user.refresh_from_db()
        assert agent_user.first_name == "Renamed"


# ---------------------------------------------------------------------------
# KZ-H01 — Export creation gated to Manager+; list scoped to owner
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestExportCreationAuthz:
    URL = "/api/v1/analytics/exports/"

    def test_agent_cannot_create_export(self, agent_client):
        resp = agent_client.post(
            self.URL, {"resource_type": "contacts", "export_type": "csv"}, format="json"
        )
        assert resp.status_code == 403

    def test_viewer_cannot_create_export(self, viewer_client):
        resp = viewer_client.post(
            self.URL, {"resource_type": "contacts", "export_type": "csv"}, format="json"
        )
        assert resp.status_code == 403

    def test_manager_can_create_export(self, manager_client, monkeypatch):
        # Don't run the real (file-writing) export task; we only assert authz.
        # The serializer does a local import of the task, so patch its .delay on
        # the shared task object in the tasks module.
        import apps.analytics.tasks as analytics_tasks

        monkeypatch.setattr(
            analytics_tasks.process_export_job, "delay", lambda *a, **k: None
        )
        resp = manager_client.post(
            self.URL, {"resource_type": "contacts", "export_type": "csv"}, format="json"
        )
        assert resp.status_code == 201

    def test_export_list_scoped_to_owner_for_agents(
        self, tenant, agent_client, manager_user
    ):
        from apps.analytics.models import ExportJob

        set_current_tenant(tenant)
        job = ExportJob.objects.create(
            tenant=tenant, resource_type="contacts", export_type="csv",
            requested_by=manager_user,
        )
        clear_current_tenant()

        resp = agent_client.get(self.URL)
        assert resp.status_code == 200
        ids = [r["id"] for r in resp.json().get("results", resp.json())]
        assert str(job.id) not in ids  # agent cannot see the manager's export job

    def test_export_list_shows_all_to_manager(self, tenant, manager_client, manager_user):
        from apps.analytics.models import ExportJob

        set_current_tenant(tenant)
        job = ExportJob.objects.create(
            tenant=tenant, resource_type="contacts", export_type="csv",
            requested_by=manager_user,
        )
        clear_current_tenant()

        resp = manager_client.get(self.URL)
        assert resp.status_code == 200
        ids = [r["id"] for r in resp.json().get("results", resp.json())]
        assert str(job.id) in ids


# ---------------------------------------------------------------------------
# KZ-H02 — Export file download authz (serve_protected_media)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestExportMediaAuthz:
    def _job(self, tenant, requester):
        from apps.analytics.models import ExportJob

        set_current_tenant(tenant)
        job = ExportJob.objects.create(
            tenant=tenant, resource_type="contacts", export_type="csv",
            requested_by=requester, file="exports/contacts_test.csv",
        )
        clear_current_tenant()
        return job

    def test_requester_can_download(self, tenant, manager_user):
        from apps.attachments.media_views import _export_path_authorized

        self._job(tenant, manager_user)
        assert _export_path_authorized(manager_user, "exports/contacts_test.csv") is True

    def test_manager_can_download_others_export(self, tenant, admin_user, manager_user):
        # admin_user requested; manager_user is Manager+ of the same tenant.
        from apps.attachments.media_views import _export_path_authorized

        self._job(tenant, admin_user)
        assert _export_path_authorized(manager_user, "exports/contacts_test.csv") is True

    def test_agent_non_requester_denied(self, tenant, manager_user, agent_user):
        from apps.attachments.media_views import _export_path_authorized

        self._job(tenant, manager_user)
        assert _export_path_authorized(agent_user, "exports/contacts_test.csv") is False

    def test_cross_tenant_user_denied(self, tenant, tenant_b, manager_user):
        from apps.attachments.media_views import _export_path_authorized

        outsider = UserFactory()
        from apps.accounts.models import Role
        MembershipFactory(
            user=outsider, tenant=tenant_b,
            role=Role.unscoped.get(tenant=tenant_b, slug="admin"),
        )
        self._job(tenant, manager_user)
        assert _export_path_authorized(outsider, "exports/contacts_test.csv") is False


# ---------------------------------------------------------------------------
# KZ-H03 — Attachment object-level media authz + list scoping
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAttachmentAuthz:
    def _attachment(self, tenant, ticket, uploader):
        from apps.attachments.models import Attachment

        set_current_tenant(tenant)
        ct = ContentType.objects.get_for_model(ticket.__class__)
        att = Attachment.objects.create(
            tenant=tenant, content_type=ct, object_id=ticket.id,
            file=f"tenants/{tenant.id}/attachments/2026/07/secret.png",
            original_name="secret.png", mime_type="image/png", size_bytes=1234,
            uploaded_by=uploader,
        )
        clear_current_tenant()
        return att

    def _setup(self, tenant, agent_role):
        set_current_tenant(tenant)
        status = TicketStatusFactory(
            tenant=tenant, name="Open", slug="open", is_default=True
        )
        assignee = UserFactory()
        MembershipFactory(user=assignee, tenant=tenant, role=agent_role)
        other = UserFactory()
        MembershipFactory(user=other, tenant=tenant, role=agent_role)
        ticket = TicketFactory(tenant=tenant, status=status, assignee=assignee,
                               created_by=assignee)
        clear_current_tenant()
        return ticket, assignee, other

    def test_assigned_agent_can_access(self, tenant, agent_role, admin_user):
        from apps.attachments.media_views import _attachment_path_authorized

        ticket, assignee, _ = self._setup(tenant, agent_role)
        att = self._attachment(tenant, ticket, admin_user)
        assert _attachment_path_authorized(assignee, att.file.name, str(tenant.id)) is True

    def test_unassigned_agent_denied(self, tenant, agent_role, admin_user):
        from apps.attachments.media_views import _attachment_path_authorized

        ticket, _, other = self._setup(tenant, agent_role)
        att = self._attachment(tenant, ticket, admin_user)
        assert _attachment_path_authorized(other, att.file.name, str(tenant.id)) is False

    def test_manager_bypasses(self, tenant, agent_role, admin_user, manager_user):
        from apps.attachments.media_views import _attachment_path_authorized

        ticket, _, _ = self._setup(tenant, agent_role)
        att = self._attachment(tenant, ticket, admin_user)
        assert _attachment_path_authorized(manager_user, att.file.name, str(tenant.id)) is True

    def test_list_excludes_unaccessible_attachments_for_agent(
        self, tenant, agent_role, admin_user
    ):
        ticket, assignee, other = self._setup(tenant, agent_role)
        att = self._attachment(tenant, ticket, admin_user)

        # 'other' agent is neither assignee nor creator nor uploader.
        client = make_api_client(other, tenant)
        resp = client.get("/api/v1/attachments/attachments/")
        assert resp.status_code == 200
        ids = [r["id"] for r in resp.json().get("results", resp.json())]
        assert str(att.id) not in ids

        # The assigned agent DOES see it.
        client2 = make_api_client(assignee, tenant)
        resp2 = client2.get("/api/v1/attachments/attachments/")
        ids2 = [r["id"] for r in resp2.json().get("results", resp2.json())]
        assert str(att.id) in ids2


# ---------------------------------------------------------------------------
# KZ-H05 — allauth local signup disabled
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAllauthSignupClosed:
    def test_adapter_closes_signup(self):
        from apps.accounts.adapters import NoLocalSignupAccountAdapter

        assert NoLocalSignupAccountAdapter().is_open_for_signup(None) is False

    def test_setting_points_at_adapter(self, settings):
        assert settings.ACCOUNT_ADAPTER == "apps.accounts.adapters.NoLocalSignupAccountAdapter"

    def test_signup_post_creates_no_user(self, client):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        email = "allauth_bypass_probe@example.com"
        client.post(
            "/accounts/signup/",
            {"email": email, "password1": "Sup3r$ecret!23", "password2": "Sup3r$ecret!23"},
            HTTP_HOST="localhost",
        )
        assert not User.objects.filter(email=email).exists()


# ---------------------------------------------------------------------------
# KZ-H06 — CallEventConsumer membership gate
# ---------------------------------------------------------------------------

def _voip_app():
    from channels.auth import AuthMiddlewareStack
    from channels.routing import URLRouter
    from django.urls import re_path

    from apps.voip.consumers import CallEventConsumer

    return AuthMiddlewareStack(
        URLRouter([re_path(r"ws/voip/events/$", CallEventConsumer.as_asgi())])
    )


def _voip_comm(user, tenant):
    from channels.testing import WebsocketCommunicator

    comm = WebsocketCommunicator(_voip_app(), "ws/voip/events/")
    comm.scope["user"] = user
    comm.scope["tenant"] = tenant
    return comm


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
class TestCallEventConsumerAuthz:
    async def test_member_can_connect(self, tenant, agent_user, settings):
        settings.CHANNEL_LAYERS = {
            "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
        }
        comm = _voip_comm(agent_user, tenant)
        connected, _ = await comm.connect()
        assert connected is True
        await comm.disconnect()

    async def test_non_member_rejected_cross_tenant(
        self, tenant, tenant_b, settings
    ):
        settings.CHANNEL_LAYERS = {
            "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
        }
        # A user who belongs to tenant_b tries to open a socket whose Host
        # resolved to `tenant` -> must be rejected (no cross-tenant call metadata).
        from channels.db import database_sync_to_async

        from apps.accounts.models import Role

        outsider = await database_sync_to_async(UserFactory)()
        await database_sync_to_async(MembershipFactory)(
            user=outsider, tenant=tenant_b,
            role=await database_sync_to_async(Role.unscoped.get)(
                tenant=tenant_b, slug="agent"
            ),
        )
        comm = _voip_comm(outsider, tenant)
        connected, _ = await comm.connect()
        assert connected is False

    async def test_anonymous_rejected(self, tenant, settings):
        settings.CHANNEL_LAYERS = {
            "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
        }
        from django.contrib.auth.models import AnonymousUser

        comm = _voip_comm(AnonymousUser(), tenant)
        connected, _ = await comm.connect()
        assert connected is False
