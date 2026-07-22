"""Regression tests for the 2026-07-20 QA-audit remediation (batches 1 & 2).

Each test asserts the SECURE behaviour introduced by a specific fix and would
FAIL against the pre-fix source. Grouped by finding. See the audit report for
the full failure scenarios.

Fixes covered here:
  * KB media pre-auth prefix + object-level gate      (attachments/media_views.py)
  * Ungated Macro / SavedView / CannedResponse         (tickets/views.py)
  * UserGroup list/retrieve downgrade                  (accounts/views.py)
  * KBSearchView missing membership check              (knowledge/views.py)
  * ContactGroup per-request tenant scoping            (contacts/views.py)
  * Comments offboarding-increases-access (Critical)   (comments/views.py)
  * Manager->Admin escalation                          (accounts/views.py)
  * CustomFieldValue ticket-visibility scoping         (custom_fields/views.py)
  * CRM Viewer-write block                             (crm/views.py)
  * Kanban Viewer-write block                          (kanban/views.py)
  * VoIP Viewer read + SIP-hijack block                (voip/views.py)
  * ChatConsumer offboarded-member block               (messaging/consumers.py)

NOT covered here (require a browser/JS runtime, not pytest): the four stored-XSS
sink fixes in templates/pages/tickets/detail.html, templates/pages/knowledge/
article.html, and static/js/command-palette.js.
"""

import uuid

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from conftest import (
    MembershipFactory,
    TicketFactory,
    UserFactory,
    make_api_client,
)
from main.context import tenant_context

from apps.accounts.models import TenantMembership, UserGroup


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _foreign_member(tenant_b):
    """An active member of an unrelated tenant (simulates a foreign JWT)."""
    return MembershipFactory(tenant=tenant_b).user


def _cross_tenant_client(user, victim_tenant):
    """Authenticate ``user`` but point the Host at the victim tenant."""
    return make_api_client(user, victim_tenant)


# ---------------------------------------------------------------------------
# Batch 1 — KB media serving (attachments/media_views.py)
# ---------------------------------------------------------------------------

def _kb_article(tenant, author, status="published", groups=None):
    from apps.knowledge.models import Article

    with tenant_context(tenant):
        a = Article.objects.create(
            tenant=tenant,
            title="Payroll",
            slug=f"payroll-{status}-{uuid.uuid4().hex[:8]}",
            author=author,
            status=status,
            file=SimpleUploadedFile("salaries.csv", b"TENANT SECRET DATA"),
        )
        if groups:
            a.allowed_groups.set(groups)
    return a


@pytest.mark.django_db
class TestKBMediaGate:
    def test_anonymous_denied(self, tenant, admin_user):
        a = _kb_article(tenant, admin_user)
        resp = Client().get(f"/media/{a.file.name}")
        assert resp.status_code == 403

    def test_owning_member_allowed(self, tenant, admin_user, agent_user):
        a = _kb_article(tenant, admin_user)
        c = Client()
        c.force_login(agent_user)
        resp = c.get(f"/media/{a.file.name}", HTTP_HOST=f"{tenant.slug}.localhost")
        assert resp.status_code == 200

    def test_foreign_tenant_member_denied(self, tenant, tenant_b, admin_user):
        a = _kb_article(tenant, admin_user)
        outsider = _foreign_member(tenant_b)
        c = Client()
        c.force_login(outsider)
        resp = c.get(f"/media/{a.file.name}", HTTP_HOST=f"{tenant.slug}.localhost")
        assert resp.status_code == 403

    def test_draft_hidden_from_non_author(self, tenant, admin_user, agent_user):
        a = _kb_article(tenant, admin_user, status="draft")
        c = Client()
        c.force_login(agent_user)
        resp = c.get(f"/media/{a.file.name}", HTTP_HOST=f"{tenant.slug}.localhost")
        assert resp.status_code == 403

    def test_allowed_groups_enforced(self, tenant, admin_user, agent_user):
        with tenant_context(tenant):
            grp = UserGroup.objects.create(tenant=tenant, name="Finance")
        a = _kb_article(tenant, admin_user, groups=[grp])
        c = Client()
        c.force_login(agent_user)
        resp = c.get(f"/media/{a.file.name}", HTTP_HOST=f"{tenant.slug}.localhost")
        assert resp.status_code == 403

    def test_logos_prefix_still_public(self):
        from apps.attachments.media_views import PUBLIC_PREFIXES

        assert PUBLIC_PREFIXES == ("tenants/logos/",)


# ---------------------------------------------------------------------------
# Batch 1 — ungated ticket viewsets + cross-tenant reads
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCrossTenantTicketViewsets:
    def test_foreign_member_cannot_read_macros(self, tenant, tenant_b, admin_user):
        from apps.tickets.models import Macro

        with tenant_context(tenant):
            Macro.objects.create(
                tenant=tenant, name="Secret", body="internal script",
                is_shared=True, created_by=admin_user,
            )
        c = _cross_tenant_client(_foreign_member(tenant_b), tenant)
        assert c.get("/api/v1/tickets/macros/").status_code == 403

    def test_foreign_member_cannot_write_macro(self, tenant, tenant_b):
        c = _cross_tenant_client(_foreign_member(tenant_b), tenant)
        resp = c.post("/api/v1/tickets/macros/", {"name": "pwn", "body": "x"}, format="json")
        assert resp.status_code == 403

    def test_foreign_member_cannot_read_saved_views(self, tenant, tenant_b):
        c = _cross_tenant_client(_foreign_member(tenant_b), tenant)
        assert c.get("/api/v1/tickets/saved-views/").status_code == 403

    def test_foreign_member_cannot_read_canned_responses(self, tenant, tenant_b):
        c = _cross_tenant_client(_foreign_member(tenant_b), tenant)
        assert c.get("/api/v1/tickets/canned-responses/").status_code == 403

    def test_no_membership_user_denied(self, tenant):
        # A user with zero memberships anywhere (the most hostile caller).
        c = make_api_client(UserFactory(), tenant)
        assert c.get("/api/v1/tickets/macros/").status_code == 403


@pytest.mark.django_db
class TestCrossTenantMisc:
    def test_foreign_member_cannot_list_user_groups(self, tenant, tenant_b):
        c = _cross_tenant_client(_foreign_member(tenant_b), tenant)
        assert c.get("/api/v1/accounts/groups/").status_code == 403

    def test_foreign_member_cannot_search_kb(self, tenant, tenant_b):
        c = _cross_tenant_client(_foreign_member(tenant_b), tenant)
        assert c.get("/api/v1/knowledge/search/?q=payroll").status_code == 403

    def test_contact_group_scoped_to_requesting_tenant(
        self, tenant, tenant_b, admin_user
    ):
        # A member of tenant_b must not see tenant's contact groups. Verifies
        # ContactGroupViewSet.get_queryset reads the tenant contextvar per
        # request (not a frozen class-body queryset).
        from apps.contacts.models import ContactGroup

        from apps.accounts.models import Role

        with tenant_context(tenant):
            ContactGroup.objects.create(tenant=tenant, name="VIPs")
        # tenant_b's roles are seeded by the create-tenant signal; reuse the
        # existing admin role rather than minting a colliding slug.
        admin_b_role = Role.unscoped.get(tenant=tenant_b, slug="admin")
        admin_b = MembershipFactory(tenant=tenant_b, role=admin_b_role).user
        c = make_api_client(admin_b, tenant_b)
        resp = c.get("/api/v1/contacts/contact-groups/")
        assert resp.status_code == 200
        names = [g["name"] for g in resp.json()["results"]]
        assert "VIPs" not in names


# ---------------------------------------------------------------------------
# Batch 2 — comments offboarding (Critical)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCommentsOffboarding:
    def test_offboarded_member_cannot_read_comments(
        self, tenant, admin_user, agent_user, default_status
    ):
        from apps.comments.models import Comment

        with tenant_context(tenant):
            t = TicketFactory(tenant=tenant, status=default_status, created_by=admin_user)
            ct = ContentType.objects.get_for_model(t.__class__)
            Comment.objects.create(
                tenant=tenant, author=admin_user, content_type=ct,
                object_id=t.id, body="INTERNAL SECRET", is_internal=True,
            )
        m = TenantMembership.objects.get(user=agent_user, tenant=tenant)

        # Active agent: 200, sees 0 rows (not their ticket).
        resp = make_api_client(agent_user, tenant).get("/api/v1/comments/comments/")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

        # Offboarded: blocked entirely (pre-fix: 200 leaking the internal body).
        m.is_active = False
        m.save()
        resp2 = make_api_client(agent_user, tenant).get("/api/v1/comments/comments/")
        assert resp2.status_code == 403

    def test_non_member_cannot_read_comments(self, tenant, tenant_b):
        c = _cross_tenant_client(_foreign_member(tenant_b), tenant)
        assert c.get("/api/v1/comments/comments/").status_code == 403


# ---------------------------------------------------------------------------
# Batch 2 — Manager->Admin escalation (create-user role guard)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCreateUserRoleGuard:
    def test_manager_cannot_mint_admin(
        self, manager_client, tenant, admin_role
    ):
        resp = manager_client.post("/api/v1/accounts/users/create-user/", {
            "email": "puppet@x.com", "password": "Str0ngP@ss!",
            "first_name": "P", "last_name": "Q", "role": str(admin_role.id),
        }, format="json")
        assert resp.status_code == 403

    def test_manager_can_create_agent(
        self, manager_client, tenant, agent_role
    ):
        resp = manager_client.post("/api/v1/accounts/users/create-user/", {
            "email": "newagent@x.com", "password": "Str0ngP@ss!",
            "first_name": "N", "last_name": "A", "role": str(agent_role.id),
        }, format="json")
        assert resp.status_code == 201


# ---------------------------------------------------------------------------
# Batch 2 — CRM Viewer-write block
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCRMViewerWrites:
    def test_viewer_cannot_create_activity(self, viewer_client):
        resp = viewer_client.post("/api/v1/crm/activities/", {
            "activity_type": "call", "subject": "x",
        }, format="json")
        assert resp.status_code == 403

    def test_agent_can_create_activity(self, agent_client):
        resp = agent_client.post("/api/v1/crm/activities/", {
            "activity_type": "call", "subject": "x",
        }, format="json")
        assert resp.status_code in (201, 400)  # allowed past authz

    def test_viewer_cannot_create_reminder(self, viewer_client):
        resp = viewer_client.post("/api/v1/crm/reminders/", {
            "subject": "x", "scheduled_at": "2030-01-01T00:00:00Z",
        }, format="json")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Batch 2 — Kanban Viewer-write block
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestKanbanViewerWrites:
    def test_viewer_cannot_create_board(self, viewer_client):
        resp = viewer_client.post("/api/v1/kanban/boards/", {
            "name": "V", "resource_type": "ticket",
        }, format="json")
        assert resp.status_code == 403

    def test_agent_can_create_board(self, agent_client):
        resp = agent_client.post("/api/v1/kanban/boards/", {
            "name": "A", "resource_type": "ticket",
        }, format="json")
        assert resp.status_code == 201


# ---------------------------------------------------------------------------
# Batch 2 — CustomFieldValue ticket-visibility scoping
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCustomFieldValueVisibility:
    def _seed(self, tenant, owner):
        from apps.custom_fields.models import CustomFieldDefinition, CustomFieldValue

        with tenant_context(tenant):
            status = __import__(
                "conftest", fromlist=["TicketStatusFactory"]
            ).TicketStatusFactory(tenant=tenant, is_default=True)
            t = TicketFactory(tenant=tenant, status=status, created_by=owner, assignee=owner)
            defn = CustomFieldDefinition.objects.create(
                tenant=tenant, module="ticket", name="SSN", slug="ssn", field_type="text",
            )
            ct = ContentType.objects.get_for_model(t.__class__)
            CustomFieldValue.objects.create(
                tenant=tenant, field=defn, content_type=ct, object_id=t.id,
                value_text="SECRET",
            )
        return t, ct

    def test_agent_cannot_read_values_for_invisible_ticket(
        self, tenant, admin_user, agent_user
    ):
        t, ct = self._seed(tenant, admin_user)  # owned by admin, not the agent
        c = make_api_client(agent_user, tenant)
        resp = c.get(
            f"/api/v1/custom-fields/values/?content_type={ct.id}&object_id={t.id}"
        )
        assert resp.status_code == 200
        results = resp.json()
        rows = results["results"] if isinstance(results, dict) else results
        assert rows == []

    def test_agent_can_read_values_for_own_ticket(self, tenant, agent_user):
        t, ct = self._seed(tenant, agent_user)  # agent is creator+assignee
        c = make_api_client(agent_user, tenant)
        resp = c.get(
            f"/api/v1/custom-fields/values/?content_type={ct.id}&object_id={t.id}"
        )
        assert resp.status_code == 200
        results = resp.json()
        rows = results["results"] if isinstance(results, dict) else results
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# Batch 2 — VoIP Viewer reads + SIP hijack
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestVoIPAuthz:
    def test_viewer_cannot_list_call_logs(self, viewer_client):
        assert viewer_client.get("/api/v1/voip/calls/").status_code == 403

    def test_viewer_cannot_download_recording(self, viewer_client):
        # Permission is checked before object lookup, so a random uuid still 403s.
        resp = viewer_client.get(f"/api/v1/voip/recordings/{uuid.uuid4()}/")
        assert resp.status_code == 403

    def test_agent_cannot_patch_extension(self, tenant, agent_user, admin_user):
        from apps.voip.models import Extension

        with tenant_context(tenant):
            ext = Extension.objects.create(
                tenant=tenant, user=admin_user, extension_number="1001",
                sip_username="admin1001", sip_password="secret",
            )
        c = make_api_client(agent_user, tenant)
        resp = c.patch(
            f"/api/v1/voip/extensions/{ext.id}/", {"user": str(agent_user.id)},
            format="json",
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Batch 2 — ChatConsumer offboarded-member block (WebSocket)
# ---------------------------------------------------------------------------

def _chat_application():
    from channels.auth import AuthMiddlewareStack
    from channels.routing import URLRouter
    from django.urls import re_path

    from apps.messaging.consumers import ChatConsumer

    return AuthMiddlewareStack(
        URLRouter([
            re_path(
                r"ws/messaging/(?P<conversation_id>[a-f0-9-]+)/$",
                ChatConsumer.as_asgi(),
            ),
        ])
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_offboarded_member_cannot_connect_chat(tenant, agent_user, agent_role):
    from channels.db import database_sync_to_async
    from channels.testing import WebsocketCommunicator

    from apps.messaging.models import Conversation, ConversationParticipant

    @database_sync_to_async
    def setup():
        with tenant_context(tenant):
            conv = Conversation.objects.create(tenant=tenant, type="direct")
            ConversationParticipant.objects.create(conversation=conv, user=agent_user)
        return conv

    @database_sync_to_async
    def offboard():
        m = TenantMembership.objects.get(user=agent_user, tenant=tenant)
        m.is_active = False
        m.save()

    conv = await setup()
    await offboard()

    app = _chat_application()
    communicator = WebsocketCommunicator(app, f"ws/messaging/{conv.pk}/")
    communicator.scope["user"] = agent_user
    communicator.scope["tenant"] = tenant
    communicator.scope["url_route"] = {"kwargs": {"conversation_id": str(conv.pk)}}

    connected, code = await communicator.connect()
    assert connected is False
    assert code == 4003
    await communicator.disconnect()
