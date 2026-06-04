"""
Inbox Hub Phase 1A tests.

Coverage:
1. Park path: flag-ON park_email_in_hub creates a HubEmail (no Ticket).
2. Convert parity: convert_to_ticket produces a Ticket field-identical
   to the legacy auto-create path (same default status, contact, tags,
   description). The HubEmail enters CONVERTED_TO_TICKET and exposes
   converted_ticket.
3. Convert idempotency: re-converting returns the existing ticket.
4. Dismiss path: terminal DISMISSED state, no Ticket created.
5. RBAC: admin sees all; manager sees all; viewer is denied write.
6. ACTION_MAP wiring: convert_to_ticket and dismiss map to the right
   codenames so HasTenantPermission grants per the seeded RBAC grid.
"""

import pytest

from conftest import (
    InboundEmailFactory,
    MembershipFactory,
    TenantFactory,
    TicketStatusFactory,
    UserFactory,
    make_api_client,
)
from main.context import clear_current_tenant, set_current_tenant


# ---------------------------------------------------------------------------
# Service-layer tests (no HTTP)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestParkEmailInHub:
    def test_park_creates_hub_email_and_marks_parked(self):
        tenant = TenantFactory(slug="park-test")
        user = UserFactory()
        from apps.accounts.models import Role
        admin_role = Role.unscoped.get(tenant=tenant, slug="admin")
        MembershipFactory(user=user, tenant=tenant, role=admin_role)

        set_current_tenant(tenant)
        try:
            tenant.settings.inbox_hub_enabled = True
            tenant.settings.save(update_fields=["inbox_hub_enabled", "updated_at"])

            TicketStatusFactory(tenant=tenant, is_default=True, name="Open", slug="open")

            inbound = InboundEmailFactory(
                recipient_email=f"support+{tenant.slug}@kanzan.io",
                tenant=tenant, subject="Park me",
            )

            from apps.inbound_email.services import process_inbound_email
            from apps.inbox_hub.models import HubEmail
            from apps.tickets.models import Ticket

            process_inbound_email(inbound.pk)
            inbound.refresh_from_db()

            assert inbound.status == "parked_in_hub"
            assert inbound.ticket_id is None
            assert HubEmail.unscoped.filter(inbound=inbound).count() == 1
            assert Ticket.unscoped.filter(tenant=tenant).count() == 0

            hub = HubEmail.unscoped.get(inbound=inbound)
            assert hub.state == HubEmail.State.NEW
            assert hub.priority == HubEmail.Priority.NORMAL
        finally:
            clear_current_tenant()

    def test_park_writes_email_received_activity_log(self):
        tenant = TenantFactory(slug="park-audit")
        user = UserFactory()
        from apps.accounts.models import Role
        admin_role = Role.unscoped.get(tenant=tenant, slug="admin")
        MembershipFactory(user=user, tenant=tenant, role=admin_role)

        set_current_tenant(tenant)
        try:
            tenant.settings.inbox_hub_enabled = True
            tenant.settings.save(update_fields=["inbox_hub_enabled", "updated_at"])
            TicketStatusFactory(tenant=tenant, is_default=True, name="Open", slug="open")

            inbound = InboundEmailFactory(
                recipient_email=f"support+{tenant.slug}@kanzan.io",
                tenant=tenant, subject="Audit me",
            )
            from apps.inbound_email.services import process_inbound_email
            from apps.comments.models import ActivityLog
            from apps.inbox_hub.models import HubEmail

            process_inbound_email(inbound.pk)
            hub = HubEmail.unscoped.get(inbound=inbound)

            from django.contrib.contenttypes.models import ContentType
            ct = ContentType.objects.get_for_model(HubEmail)
            logs = ActivityLog.unscoped.filter(
                content_type=ct, object_id=hub.id,
                action=ActivityLog.Action.EMAIL_RECEIVED,
            )
            assert logs.count() == 1
            assert "parked in Inbox Hub" in logs.first().description
        finally:
            clear_current_tenant()


@pytest.mark.django_db
class TestConvertToTicketParity:
    """The conversion path MUST produce a ticket field-identical to the
    legacy auto-create path. This is the product-correctness guarantee
    of Phase 1A — agents convert without losing any data the legacy
    flow would have captured."""

    def _seed(self, slug):
        tenant = TenantFactory(slug=slug)
        user = UserFactory()
        from apps.accounts.models import Role
        admin_role = Role.unscoped.get(tenant=tenant, slug="admin")
        MembershipFactory(user=user, tenant=tenant, role=admin_role)
        set_current_tenant(tenant)
        TicketStatusFactory(tenant=tenant, is_default=True, name="Open", slug="open")
        return tenant, user

    def test_convert_produces_same_ticket_as_legacy(self):
        """Run both paths against equivalent inputs and diff the tickets."""
        from apps.inbox_hub.models import HubEmail
        from apps.inbox_hub.services import convert_to_ticket
        from apps.inbound_email.services import process_inbound_email

        # ---- Legacy path (flag OFF) ----
        legacy_tenant, legacy_user = self._seed("legacy")
        legacy_tenant.settings.inbox_hub_enabled = False
        legacy_tenant.settings.save(update_fields=["inbox_hub_enabled", "updated_at"])
        legacy_inbound = InboundEmailFactory(
            recipient_email=f"support+{legacy_tenant.slug}@kanzan.io",
            tenant=legacy_tenant, sender_email="customer@example.com",
            subject="Need help", body_text="Body content",
        )
        process_inbound_email(legacy_inbound.pk)
        legacy_inbound.refresh_from_db()
        legacy_ticket = legacy_inbound.ticket
        clear_current_tenant()

        # ---- Hub path (flag ON → park → convert) ----
        hub_tenant, hub_user = self._seed("hubconv")
        hub_tenant.settings.inbox_hub_enabled = True
        hub_tenant.settings.save(update_fields=["inbox_hub_enabled", "updated_at"])
        hub_inbound = InboundEmailFactory(
            recipient_email=f"support+{hub_tenant.slug}@kanzan.io",
            tenant=hub_tenant, sender_email="customer@example.com",
            subject="Need help", body_text="Body content",
        )
        process_inbound_email(hub_inbound.pk)
        hub_email = HubEmail.unscoped.get(inbound=hub_inbound)
        hub_ticket = convert_to_ticket(hub_email, actor=hub_user)
        clear_current_tenant()

        # ---- Parity assertions ----
        assert legacy_ticket.subject == hub_ticket.subject
        assert legacy_ticket.description == hub_ticket.description
        assert legacy_ticket.tags == hub_ticket.tags  # both ["email"]
        assert legacy_ticket.custom_data["source"] == hub_ticket.custom_data["source"] == "email"
        assert legacy_ticket.contact.email == hub_ticket.contact.email
        assert legacy_ticket.status.slug == hub_ticket.status.slug
        # The actor diverges intentionally: legacy uses system_user, hub
        # uses the human who clicked Convert. This is the desired audit
        # difference — we assert the hub actor is the test user.
        assert hub_ticket.created_by == hub_user

        # HubEmail bookkeeping
        hub_email.refresh_from_db()
        assert hub_email.state == HubEmail.State.CONVERTED_TO_TICKET
        assert hub_email.converted_ticket_id == hub_ticket.id

    def test_convert_is_idempotent(self):
        from apps.inbox_hub.models import HubEmail
        from apps.inbox_hub.services import convert_to_ticket
        from apps.inbound_email.services import process_inbound_email

        tenant, user = self._seed("idem")
        tenant.settings.inbox_hub_enabled = True
        tenant.settings.save(update_fields=["inbox_hub_enabled", "updated_at"])
        inbound = InboundEmailFactory(
            recipient_email=f"support+{tenant.slug}@kanzan.io",
            tenant=tenant,
        )
        process_inbound_email(inbound.pk)
        hub = HubEmail.unscoped.get(inbound=inbound)

        t1 = convert_to_ticket(hub, actor=user)
        t2 = convert_to_ticket(hub, actor=user)
        assert t1.pk == t2.pk
        clear_current_tenant()

    def test_convert_applies_priority_override(self):
        from apps.inbox_hub.models import HubEmail
        from apps.inbox_hub.services import convert_to_ticket
        from apps.inbound_email.services import process_inbound_email

        tenant, user = self._seed("override")
        tenant.settings.inbox_hub_enabled = True
        tenant.settings.save(update_fields=["inbox_hub_enabled", "updated_at"])
        inbound = InboundEmailFactory(
            recipient_email=f"support+{tenant.slug}@kanzan.io",
            tenant=tenant,
        )
        process_inbound_email(inbound.pk)
        hub = HubEmail.unscoped.get(inbound=inbound)

        ticket = convert_to_ticket(hub, actor=user, priority="urgent")
        assert ticket.priority == "urgent"
        clear_current_tenant()


@pytest.mark.django_db
class TestDismissHubEmail:
    def test_dismiss_marks_state_and_writes_log(self):
        from apps.comments.models import ActivityLog
        from apps.inbox_hub.models import HubEmail
        from apps.inbox_hub.services import dismiss_hub_email
        from apps.inbound_email.services import process_inbound_email

        tenant = TenantFactory(slug="dismiss-test")
        user = UserFactory()
        from apps.accounts.models import Role
        admin_role = Role.unscoped.get(tenant=tenant, slug="admin")
        MembershipFactory(user=user, tenant=tenant, role=admin_role)
        set_current_tenant(tenant)
        try:
            tenant.settings.inbox_hub_enabled = True
            tenant.settings.save(update_fields=["inbox_hub_enabled", "updated_at"])
            TicketStatusFactory(tenant=tenant, is_default=True, name="Open", slug="open")
            inbound = InboundEmailFactory(
                recipient_email=f"support+{tenant.slug}@kanzan.io",
                tenant=tenant,
            )
            process_inbound_email(inbound.pk)
            hub = HubEmail.unscoped.get(inbound=inbound)

            dismiss_hub_email(hub, actor=user, reason="vendor spam")
            hub.refresh_from_db()

            assert hub.state == HubEmail.State.DISMISSED
            assert hub.dismissed_at is not None
            assert hub.dismissed_by == user
            assert hub.dismissal_reason == "vendor spam"

            from django.contrib.contenttypes.models import ContentType
            ct = ContentType.objects.get_for_model(HubEmail)
            logs = ActivityLog.unscoped.filter(
                content_type=ct, object_id=hub.id,
                action=ActivityLog.Action.EMAIL_DISMISSED,
            )
            assert logs.count() == 1
            assert logs.first().changes == {"reason": "vendor spam"}
        finally:
            clear_current_tenant()


# ---------------------------------------------------------------------------
# HTTP / RBAC tests
# ---------------------------------------------------------------------------


@pytest.fixture
def parked_hub_email(tenant, default_status, admin_user):
    """A HubEmail in NEW state, already parked, ready for HTTP-level tests.

    Depends on ``admin_user`` so that ``process_inbound_email`` has at
    least one active TenantMembership to pick as the system-user actor —
    every consumer of this fixture is implicitly tested against a tenant
    that has at least one admin, which mirrors real-world tenants.
    """
    set_current_tenant(tenant)
    try:
        tenant.settings.inbox_hub_enabled = True
        tenant.settings.save(update_fields=["inbox_hub_enabled", "updated_at"])
        inbound = InboundEmailFactory(
            recipient_email=f"support+{tenant.slug}@kanzan.io",
            tenant=tenant, subject="Triage me", body_text="Body",
        )
        from apps.inbound_email.services import process_inbound_email
        from apps.inbox_hub.models import HubEmail
        process_inbound_email(inbound.pk)
        return HubEmail.unscoped.get(inbound=inbound)
    finally:
        clear_current_tenant()


@pytest.mark.django_db
class TestHubEmailApiPermissions:
    def test_admin_can_list(self, admin_client, parked_hub_email):
        resp = admin_client.get("/api/v1/inbox-hub/hub-emails/")
        assert resp.status_code == 200
        assert resp.data["count"] >= 1

    def test_manager_can_list(self, manager_client, parked_hub_email):
        resp = manager_client.get("/api/v1/inbox-hub/hub-emails/")
        assert resp.status_code == 200

    def test_admin_can_convert(self, admin_client, parked_hub_email):
        resp = admin_client.post(
            f"/api/v1/inbox-hub/hub-emails/{parked_hub_email.id}/convert-to-ticket/",
            {}, format="json",
        )
        assert resp.status_code == 201
        assert "ticket" in resp.data
        assert "hub_email" in resp.data

    def test_admin_can_dismiss(self, admin_client, parked_hub_email):
        resp = admin_client.post(
            f"/api/v1/inbox-hub/hub-emails/{parked_hub_email.id}/dismiss/",
            {"reason": "spam"}, format="json",
        )
        assert resp.status_code == 200
        assert resp.data["state"] == "dismissed"

    def test_viewer_cannot_convert(self, viewer_client, parked_hub_email):
        """Viewer has hub_email.view via the ≤40 fallback but NOT
        hub_email.convert — the seeded role has no permissions at all,
        and the fallback grants view only."""
        resp = viewer_client.post(
            f"/api/v1/inbox-hub/hub-emails/{parked_hub_email.id}/convert-to-ticket/",
            {}, format="json",
        )
        assert resp.status_code == 403

    def test_anon_denied(self, anon_client, parked_hub_email):
        resp = anon_client.get("/api/v1/inbox-hub/hub-emails/")
        assert resp.status_code in (401, 403)

    def test_cross_tenant_isolation(self, admin_client, parked_hub_email, tenant_b):
        """An admin on tenant A cannot see tenant B's HubEmails via the
        tenant A subdomain — the TenantAwareManager filters them out.

        Creates the tenant_b HubEmail directly via the ORM rather than
        running ``process_inbound_email`` so the test stays focused on
        cross-tenant isolation and doesn't need to also seed a tenant_b
        admin user, contact, ticket status, etc.
        """
        from apps.inbox_hub.models import HubEmail
        from apps.inbound_email.models import InboundEmail

        other_inbound = InboundEmail.objects.create(
            tenant=tenant_b,
            sender_email="other@example.com",
            recipient_email=f"support+{tenant_b.slug}@kanzan.io",
            subject="Other tenant email",
            body_text="Other body",
            message_id=f"cross-tenant-test@{tenant_b.slug}",
            raw_headers="",
        )
        other_hub = HubEmail.unscoped.create(
            tenant=tenant_b, inbound=other_inbound,
            state=HubEmail.State.NEW, priority=HubEmail.Priority.NORMAL,
        )

        resp = admin_client.get("/api/v1/inbox-hub/hub-emails/")
        assert resp.status_code == 200
        ids = [row["id"] for row in resp.data["results"]]
        assert str(other_hub.id) not in ids
        assert str(parked_hub_email.id) in ids
