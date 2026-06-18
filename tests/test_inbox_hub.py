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

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from conftest import (
    CompanyFactory,
    ContactFactory,
    InboundEmailFactory,
    MembershipFactory,
    TenantFactory,
    TicketFactory,
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

    def test_convert_applies_field_overrides(self):
        """Agent overrides (subject/description/category/tags) land on the
        ticket; the 'email' provenance tag is preserved alongside agent tags."""
        from apps.inbox_hub.models import HubEmail
        from apps.inbox_hub.services import convert_to_ticket
        from apps.inbound_email.services import process_inbound_email

        tenant, user = self._seed("fieldover")
        tenant.settings.inbox_hub_enabled = True
        tenant.settings.save(update_fields=["inbox_hub_enabled", "updated_at"])
        inbound = InboundEmailFactory(
            recipient_email=f"support+{tenant.slug}@kanzan.io",
            tenant=tenant, subject="raw subject", body_text="raw body",
        )
        process_inbound_email(inbound.pk)
        hub = HubEmail.unscoped.get(inbound=inbound)

        ticket = convert_to_ticket(
            hub, actor=user,
            subject="Refined subject", description="Proper description",
            category="Billing", priority="high", tags=["vip"],
        )
        assert ticket.subject == "Refined subject"
        assert ticket.description == "Proper description"
        assert ticket.category == "Billing"
        assert ticket.priority == "high"
        assert "email" in ticket.tags  # provenance preserved
        assert "vip" in ticket.tags    # agent tag appended
        clear_current_tenant()

    def test_convert_priority_override_seeds_matching_sla(self):
        """Regression: SLA deadlines must reflect the OVERRIDDEN priority, not
        the model default. initialize_sla runs after the override is folded
        into creation, so the urgent policy (not medium) is attached."""
        from apps.inbox_hub.models import HubEmail
        from apps.inbox_hub.services import convert_to_ticket
        from apps.inbound_email.services import process_inbound_email
        from apps.tickets.models import SLAPolicy

        tenant, user = self._seed("slapri")
        tenant.settings.inbox_hub_enabled = True
        tenant.settings.save(update_fields=["inbox_hub_enabled", "updated_at"])
        SLAPolicy.unscoped.create(
            tenant=tenant, name="Medium", priority="medium",
            first_response_minutes=600, resolution_minutes=1200,
            business_hours_only=False,
        )
        urgent = SLAPolicy.unscoped.create(
            tenant=tenant, name="Urgent", priority="urgent",
            first_response_minutes=10, resolution_minutes=20,
            business_hours_only=False,
        )
        inbound = InboundEmailFactory(
            recipient_email=f"support+{tenant.slug}@kanzan.io", tenant=tenant,
        )
        process_inbound_email(inbound.pk)
        hub = HubEmail.unscoped.get(inbound=inbound)

        ticket = convert_to_ticket(hub, actor=user, priority="urgent")
        assert ticket.priority == "urgent"
        assert ticket.sla_policy_id == urgent.id
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

    def test_manager_can_list_without_department(self, manager_client, parked_hub_email):
        """Managers are the supervisory see-all tier — they bypass the
        department gate entirely (no department/group membership required)."""
        resp = manager_client.get("/api/v1/inbox-hub/hub-emails/")
        assert resp.status_code == 200
        assert resp.data["count"] >= 1

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
        """Viewer (level 40) is below the agent floor — fully gated out of the
        Hub, so convert is denied at the access gate."""
        resp = viewer_client.post(
            f"/api/v1/inbox-hub/hub-emails/{parked_hub_email.id}/convert-to-ticket/",
            {}, format="json",
        )
        assert resp.status_code == 403

    def test_viewer_cannot_list(self, viewer_client, parked_hub_email):
        """Role floor: a Viewer can't even read the backlog, even in a tenant
        with no departments (closes the old 'grouped Viewer reads all' hole)."""
        resp = viewer_client.get("/api/v1/inbox-hub/hub-emails/")
        assert resp.status_code == 403

    def test_viewer_in_department_still_denied(
        self, viewer_client, viewer_user, tenant, admin_user, parked_hub_email
    ):
        """Even if an admin mistakenly adds a Viewer to a department, the role
        floor keeps them out — department membership never overrides it."""
        dept = _make_department(tenant, admin_user, slug="support")
        _add_to_department(tenant, dept, viewer_user)
        resp = viewer_client.get("/api/v1/inbox-hub/hub-emails/")
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


# ---------------------------------------------------------------------------
# Attachments — customer-sent files surface on the detail + stream via an
# authed, index-addressed download action (never the raw storage path).
# ---------------------------------------------------------------------------


@pytest.fixture
def hub_email_with_attachments(parked_hub_email):
    """A parked HubEmail whose inbound carries one image + one non-image file,
    both persisted to default_storage. Files are cleaned up after the test."""
    from django.core.files.base import ContentFile
    from django.core.files.storage import default_storage

    inbound = parked_hub_email.inbound
    img_path = default_storage.save(
        f"inbound_emails/{inbound.pk}/photo.png", ContentFile(b"\x89PNG\r\n\x1a\nfake"),
    )
    doc_path = default_storage.save(
        f"inbound_emails/{inbound.pk}/notes.txt", ContentFile(b"hello world"),
    )
    inbound.attachment_metadata = [
        {"filename": "photo.png", "content_type": "image/png",
         "size": 9, "storage_path": img_path},
        {"filename": "notes.txt", "content_type": "text/plain",
         "size": 11, "storage_path": doc_path},
    ]
    inbound.save(update_fields=["attachment_metadata", "updated_at"])
    yield parked_hub_email
    for p in (img_path, doc_path):
        if default_storage.exists(p):
            default_storage.delete(p)


@pytest.mark.django_db
class TestHubEmailAttachments:
    def _detail(self, client, hub_email):
        return client.get(f"/api/v1/inbox-hub/hub-emails/{hub_email.id}/")

    def test_detail_exposes_attachments(self, admin_client, hub_email_with_attachments):
        resp = self._detail(admin_client, hub_email_with_attachments)
        assert resp.status_code == 200
        atts = resp.data["attachments"]
        assert len(atts) == 2
        img, doc = atts[0], atts[1]
        assert img["filename"] == "photo.png"
        assert img["is_image"] is True
        assert img["url"].endswith("/attachment/?i=0")
        assert doc["is_image"] is False
        assert doc["url"].endswith("/attachment/?i=1")

    def test_list_row_summarises_attachments(self, admin_client, hub_email_with_attachments):
        """List rows carry has/count (not the full list) so the paperclip + count
        can render without the detail round-trip."""
        resp = admin_client.get("/api/v1/inbox-hub/hub-emails/")
        row = next(r for r in resp.data["results"]
                   if r["id"] == str(hub_email_with_attachments.id))
        assert row["has_attachments"] is True
        assert row["attachment_count"] == 2
        assert "attachments" not in row  # detail-only

    def test_download_image_served_inline(self, admin_client, hub_email_with_attachments):
        resp = admin_client.get(
            f"/api/v1/inbox-hub/hub-emails/{hub_email_with_attachments.id}/attachment/?i=0"
        )
        assert resp.status_code == 200
        assert resp["Content-Type"] == "image/png"
        assert resp["X-Content-Type-Options"] == "nosniff"
        # Inline (embeddable in <img>), not a forced download.
        assert "attachment" not in (resp.get("Content-Disposition") or "")
        assert b"".join(resp.streaming_content) == b"\x89PNG\r\n\x1a\nfake"

    def test_download_nonimage_forced_attachment(self, admin_client, hub_email_with_attachments):
        resp = admin_client.get(
            f"/api/v1/inbox-hub/hub-emails/{hub_email_with_attachments.id}/attachment/?i=1"
        )
        assert resp.status_code == 200
        assert "attachment" in (resp.get("Content-Disposition") or "")
        assert resp["X-Content-Type-Options"] == "nosniff"

    def test_image_force_download_with_dl_flag(self, admin_client, hub_email_with_attachments):
        resp = admin_client.get(
            f"/api/v1/inbox-hub/hub-emails/{hub_email_with_attachments.id}/attachment/?i=0&dl=1"
        )
        assert resp.status_code == 200
        assert "attachment" in (resp.get("Content-Disposition") or "")

    def test_download_index_out_of_range_404(self, admin_client, hub_email_with_attachments):
        resp = admin_client.get(
            f"/api/v1/inbox-hub/hub-emails/{hub_email_with_attachments.id}/attachment/?i=9"
        )
        assert resp.status_code == 404

    def test_download_missing_file_404(self, admin_client, hub_email_with_attachments):
        """Once the email is converted, the bytes move to a Ticket Attachment and
        the inbound storage path is deleted — a stale link must 404, not 500."""
        from django.core.files.storage import default_storage

        default_storage.delete(
            hub_email_with_attachments.inbound.attachment_metadata[0]["storage_path"]
        )
        resp = admin_client.get(
            f"/api/v1/inbox-hub/hub-emails/{hub_email_with_attachments.id}/attachment/?i=0"
        )
        assert resp.status_code == 404

    def test_anon_cannot_download(self, anon_client, hub_email_with_attachments):
        resp = anon_client.get(
            f"/api/v1/inbox-hub/hub-emails/{hub_email_with_attachments.id}/attachment/?i=0"
        )
        assert resp.status_code in (401, 403)

    def test_viewer_cannot_download(self, viewer_client, hub_email_with_attachments):
        """The Hub access gate (role floor) also guards the file stream."""
        resp = viewer_client.get(
            f"/api/v1/inbox-hub/hub-emails/{hub_email_with_attachments.id}/attachment/?i=0"
        )
        assert resp.status_code == 403


@pytest.mark.django_db
class TestCreateTicketFromEmailApi:
    """POST /api/v1/inbound-email/{id}/create-ticket/ — the agent Emails-page
    'Create ticket' form posting field overrides instead of one-click create."""

    def _url(self, hub_email):
        return f"/api/v1/inbound-email/{hub_email.inbound_id}/create-ticket/"

    def test_applies_overrides(self, admin_client, parked_hub_email):
        resp = admin_client.post(
            self._url(parked_hub_email),
            {
                "subject": "Refined subject",
                "description": "A proper description.",
                "priority": "high",
                "category": "Billing",
            },
            format="json",
        )
        assert resp.status_code == 201, resp.data
        from apps.tickets.models import Ticket

        ticket = Ticket.unscoped.get(pk=resp.data["ticket_id"])
        assert ticket.subject == "Refined subject"
        assert ticket.description == "A proper description."
        assert ticket.priority == "high"
        assert ticket.category == "Billing"

    def test_blank_subject_falls_back_to_email_subject(self, admin_client, parked_hub_email):
        """Clearing the subject keeps the email's subject rather than creating
        a blank-titled ticket (preserves the old one-click semantics)."""
        resp = admin_client.post(
            self._url(parked_hub_email), {"subject": "  "}, format="json",
        )
        assert resp.status_code == 201, resp.data
        assert resp.data["ticket_subject"] == "Triage me"  # from the fixture

    def test_rejects_closed_status(self, admin_client, parked_hub_email, tenant):
        set_current_tenant(tenant)
        closed = TicketStatusFactory(
            tenant=tenant, name="Closed", slug="closed-x", is_closed=True,
        )
        clear_current_tenant()
        resp = admin_client.post(
            self._url(parked_hub_email), {"status": str(closed.id)}, format="json",
        )
        assert resp.status_code == 400
        assert "status" in resp.data

    def test_malformed_reference_returns_400_not_500(self, admin_client, parked_hub_email):
        """A non-UUID queue id must be a clean 400, not an unhandled 500."""
        resp = admin_client.post(
            self._url(parked_hub_email), {"queue": "not-a-uuid"}, format="json",
        )
        assert resp.status_code == 400

    def test_invalid_priority_returns_400(self, admin_client, parked_hub_email):
        resp = admin_client.post(
            self._url(parked_hub_email), {"priority": "bogus"}, format="json",
        )
        assert resp.status_code == 400
        assert "priority" in resp.data


@pytest.mark.django_db
class TestHubConvertOverrides:
    """POST /api/v1/inbox-hub/hub-emails/{id}/convert-to-ticket/ — the cockpit
    'Convert to ticket' panel now posts the FULL override set, validated by the
    same shared build_ticket_overrides as the Emails-page form (so the two stay
    in lock-step). Previously the Hub forwarded only queue/status/assignee/
    priority; subject/description/category/due_date/tags were unreachable here."""

    def _url(self, hub_email):
        return f"/api/v1/inbox-hub/hub-emails/{hub_email.id}/convert-to-ticket/"

    def _ticket(self, hub_email):
        from apps.tickets.models import Ticket

        hub_email.refresh_from_db()
        return Ticket.unscoped.get(pk=hub_email.converted_ticket_id)

    def test_full_overrides_applied(self, admin_client, parked_hub_email):
        resp = admin_client.post(
            self._url(parked_hub_email),
            {
                "subject": "Refined subject",
                "description": "<p>A proper description.</p>",
                "priority": "high",
                "category": "Billing",
                "tags": ["vip", "billing"],
            },
            format="json",
        )
        assert resp.status_code == 201, resp.data
        ticket = self._ticket(parked_hub_email)
        assert ticket.subject == "Refined subject"
        assert ticket.description == "<p>A proper description.</p>"
        assert ticket.priority == "high"
        assert ticket.category == "Billing"
        # The 'email' provenance tag survives alongside the agent's tags.
        assert "email" in ticket.tags
        assert "vip" in ticket.tags and "billing" in ticket.tags

    def test_blank_subject_keeps_email_subject(self, admin_client, parked_hub_email):
        resp = admin_client.post(
            self._url(parked_hub_email), {"subject": "  "}, format="json",
        )
        assert resp.status_code == 201, resp.data
        assert self._ticket(parked_hub_email).subject == "Triage me"  # fixture subject

    def test_invalid_priority_rejected(self, admin_client, parked_hub_email):
        # 'normal' is the HubEmail vocab — NOT a Ticket priority. The old modal
        # offered it and 400'd; the panel now maps it to 'medium', but the
        # server must still reject a raw 'normal'.
        resp = admin_client.post(
            self._url(parked_hub_email), {"priority": "normal"}, format="json",
        )
        assert resp.status_code == 400
        assert "priority" in resp.data

    def test_rejects_closed_status(self, admin_client, parked_hub_email, tenant):
        set_current_tenant(tenant)
        closed = TicketStatusFactory(
            tenant=tenant, name="Closed", slug="closed-hub", is_closed=True,
        )
        clear_current_tenant()
        resp = admin_client.post(
            self._url(parked_hub_email), {"status": str(closed.id)}, format="json",
        )
        assert resp.status_code == 400
        assert "status" in resp.data

    def test_malformed_queue_returns_400(self, admin_client, parked_hub_email):
        resp = admin_client.post(
            self._url(parked_hub_email), {"queue": "not-a-uuid"}, format="json",
        )
        assert resp.status_code == 400

    def test_non_member_assignee_returns_400(self, admin_client, parked_hub_email):
        outsider = UserFactory()
        resp = admin_client.post(
            self._url(parked_hub_email), {"assignee": str(outsider.id)}, format="json",
        )
        assert resp.status_code == 400
        assert "assignee" in resp.data


# ---------------------------------------------------------------------------
# Triage cockpit: enriched serializer + Hub-local customer-context action
# ---------------------------------------------------------------------------


def _build_hub_email(
    tenant,
    *,
    contact=None,
    body_text="Body",
    body_html="",
    attachment_metadata=None,
    sla_response_due_at=None,
    response_breached=False,
    state=None,
    department=None,
    assignee=None,
):
    """Create a parked HubEmail directly via the ORM (explicit tenant), so the
    cockpit serializer/context tests can control body, attachments and SLA
    fields without driving the whole inbound pipeline."""
    from apps.inbound_email.models import InboundEmail
    from apps.inbox_hub.models import HubEmail

    inbound = InboundEmail.objects.create(
        tenant=tenant,
        sender_email="customer@example.com",
        recipient_email=f"support+{tenant.slug}@kanzan.io",
        subject="Hello there",
        body_text=body_text,
        body_html=body_html,
        attachment_metadata=attachment_metadata or [],
        message_id=f"{uuid.uuid4()}@test.com",
        raw_headers="",
    )
    return HubEmail.unscoped.create(
        tenant=tenant,
        inbound=inbound,
        contact=contact,
        state=state or HubEmail.State.NEW,
        priority=HubEmail.Priority.NORMAL,
        sla_response_due_at=sla_response_due_at,
        response_breached=response_breached,
        department=department,
        assignee=assignee,
    )


def _make_department(tenant, lead, *, slug, name=None):
    """Create an active Department with the given lead (PROTECT FK)."""
    from apps.inbox_hub.models import Department

    return Department.unscoped.create(
        tenant=tenant, name=name or slug.title(), slug=slug, lead=lead,
    )


def _add_to_department(tenant, department, user):
    from apps.inbox_hub.models import DepartmentMembership

    return DepartmentMembership.unscoped.create(
        tenant=tenant, department=department, user=user,
    )


@pytest.mark.django_db
class TestCockpitSerializer:
    def test_list_row_exposes_cockpit_fields(self, admin_client, tenant):
        contact = ContactFactory(tenant=tenant, email="customer@example.com")
        hub = _build_hub_email(
            tenant,
            contact=contact,
            body_text="Hello world from the customer",
            attachment_metadata=[
                {"filename": "a.pdf", "content_type": "application/pdf", "size": 10},
                {"filename": "b.png", "content_type": "image/png", "size": 20},
            ],
            sla_response_due_at=timezone.now() + timedelta(hours=1),
        )

        resp = admin_client.get("/api/v1/inbox-hub/hub-emails/?state=new")
        assert resp.status_code == 200
        row = next(r for r in resp.data["results"] if r["id"] == str(hub.id))

        assert row["contact_id"] == str(contact.id)
        assert "Hello world from the customer" in row["snippet"]
        assert row["has_attachments"] is True
        assert row["attachment_count"] == 2
        assert "sla_response_due_at" in row  # promoted onto the list serializer

    def test_snippet_strips_html_and_truncates(self, admin_client, tenant):
        long_html = "<p>Hello <b>world</b> " + ("spam " * 200) + "</p>"
        hub = _build_hub_email(tenant, body_text="", body_html=long_html)

        resp = admin_client.get("/api/v1/inbox-hub/hub-emails/?state=new")
        row = next(r for r in resp.data["results"] if r["id"] == str(hub.id))

        assert "<" not in row["snippet"]            # tags stripped
        assert row["snippet"].startswith("Hello world")
        assert len(row["snippet"]) <= 141           # Truncator adds an ellipsis char

    def test_unknown_sender_has_null_contact_id(self, admin_client, tenant):
        hub = _build_hub_email(tenant, contact=None)
        resp = admin_client.get("/api/v1/inbox-hub/hub-emails/?state=new")
        row = next(r for r in resp.data["results"] if r["id"] == str(hub.id))
        assert row["contact_id"] is None


@pytest.mark.django_db
class TestHubEmailContextAction:
    def test_context_happy_path(self, admin_client, tenant, default_status, closed_status):
        from apps.contacts.models import Account

        company = CompanyFactory(tenant=tenant, name="Acme Co")
        account = Account.unscoped.create(
            tenant=tenant, name="Acme Account",
            mrr=Decimal("4200.00"), health_score=88,
        )
        contact = ContactFactory(
            tenant=tenant, email="customer@example.com",
            company=company, account=account, email_bouncing=True,
        )
        # Two tickets for this contact: one open, one closed.
        TicketFactory(tenant=tenant, contact=contact, status=default_status, csat_rating=4)
        TicketFactory(tenant=tenant, contact=contact, status=closed_status)

        hub = _build_hub_email(tenant, contact=contact)

        resp = admin_client.get(f"/api/v1/inbox-hub/hub-emails/{hub.id}/context/")
        assert resp.status_code == 200
        data = resp.data
        assert data["contact"]["company"] == "Acme Co"
        assert data["contact"]["account"]["health_score"] == 88
        assert data["contact"]["account"]["mrr"] == "4200.00"
        assert data["contact"]["email_bouncing"] is True
        assert data["stats"]["total_tickets"] == 2
        assert data["stats"]["open_tickets"] == 1
        assert len(data["recent_tickets"]) == 2

    def test_context_null_contact_returns_empty(self, admin_client, tenant):
        hub = _build_hub_email(tenant, contact=None)
        resp = admin_client.get(f"/api/v1/inbox-hub/hub-emails/{hub.id}/context/")
        assert resp.status_code == 200
        assert resp.data["contact"] is None

    def test_agent_can_reach_hub_context_but_not_contacts_context(
        self, agent_client, agent_user, tenant
    ):
        """The load-bearing design decision: an Agent (level 30) who owns no
        ticket for the contact CAN read the Hub-local context action (its
        row-scoping is the Hub's own), while the contacts endpoint 404s them
        (it scopes contacts to tickets the agent created/owns)."""
        # This tenant has no departments, so the access gate falls open for
        # agent-tier; the NEW unrouted HubEmail is then visible via
        # IsHubEmailAccessible's row-scope (NEW + no department = shared pool).
        contact = ContactFactory(tenant=tenant, email="stranger@example.com")
        hub = _build_hub_email(tenant, contact=contact)

        hub_resp = agent_client.get(f"/api/v1/inbox-hub/hub-emails/{hub.id}/context/")
        assert hub_resp.status_code == 200          # reachable via Hub row-scope

        contacts_resp = agent_client.get(
            f"/api/v1/contacts/contacts/{contact.id}/context/"
        )
        assert contacts_resp.status_code == 404      # scoped out — why we needed the Hub action


@pytest.mark.django_db
class TestSlaRiskLens:
    def test_sla_risk_filters_and_orders(self, admin_client, tenant):
        now = timezone.now()
        e_a = _build_hub_email(tenant, sla_response_due_at=now + timedelta(minutes=60))
        e_b = _build_hub_email(tenant, sla_response_due_at=now + timedelta(minutes=10))
        # Excluded: breached deadline, and no deadline at all.
        _build_hub_email(
            tenant, sla_response_due_at=now + timedelta(minutes=5),
            response_breached=True,
        )
        _build_hub_email(tenant, sla_response_due_at=None)

        resp = admin_client.get(
            "/api/v1/inbox-hub/hub-emails/?sla_risk=true&ordering=sla_response_due_at"
        )
        assert resp.status_code == 200
        ids = [r["id"] for r in resp.data["results"]]
        # Only the two live deadlines, soonest first.
        assert ids == [str(e_b.id), str(e_a.id)]


@pytest.mark.django_db
class TestDepartmentScopedVisibility:
    """The team-inbox access model: agents see their own department(s)' NEW
    backlog (plus unrouted mail and anything assigned to them); they do NOT see
    another department's email. Supervisors (Manager+) see everything."""

    def _ids(self, resp):
        return {r["id"] for r in resp.data["results"]}

    def test_agent_sees_own_department_not_another(
        self, agent_client, agent_user, tenant, admin_user
    ):
        sales = _make_department(tenant, admin_user, slug="sales")
        support = _make_department(tenant, admin_user, slug="support")
        _add_to_department(tenant, sales, agent_user)

        mine = _build_hub_email(tenant, department=sales)
        theirs = _build_hub_email(tenant, department=support)
        shared = _build_hub_email(tenant, department=None)  # unrouted pool

        resp = agent_client.get("/api/v1/inbox-hub/hub-emails/")
        assert resp.status_code == 200
        ids = self._ids(resp)
        assert str(mine.id) in ids
        assert str(shared.id) in ids       # shared pool visible to every agent
        assert str(theirs.id) not in ids   # another department's mail is hidden

    def test_department_filter_param_cannot_widen_scope(
        self, agent_client, agent_user, tenant, admin_user
    ):
        """A crafted ?department=<other> must not surface another team's mail —
        the scope is enforced server-side, the param only narrows within it."""
        sales = _make_department(tenant, admin_user, slug="sales")
        support = _make_department(tenant, admin_user, slug="support")
        _add_to_department(tenant, sales, agent_user)
        theirs = _build_hub_email(tenant, department=support)

        resp = agent_client.get(
            f"/api/v1/inbox-hub/hub-emails/?department={support.id}"
        )
        assert resp.status_code == 200
        assert str(theirs.id) not in self._ids(resp)

    def test_assigned_email_in_other_department_is_visible(
        self, agent_client, agent_user, tenant, admin_user
    ):
        """Black-hole fix: an agent assigned mail in a department they're not a
        member of can still SEE it (assignee=me crosses the boundary) AND can
        open the Hub at all even though departments exist."""
        from apps.inbox_hub.models import HubEmail

        sales = _make_department(tenant, admin_user, slug="sales")
        # agent_user is in NO department.
        assigned = _build_hub_email(
            tenant, department=sales, assignee=agent_user,
            state=HubEmail.State.ASSIGNED,
        )

        resp = agent_client.get("/api/v1/inbox-hub/hub-emails/")
        assert resp.status_code == 200          # gate safety-valve let them in
        assert str(assigned.id) in self._ids(resp)

    def test_agent_in_dept_does_not_see_others_in_flight(
        self, agent_client, agent_user, tenant, admin_user
    ):
        """Within a shared department the agent sees the NEW backlog, but an
        in-flight email assigned to a teammate is not theirs to see (only NEW
        or assigned-to-me cross the line)."""
        from apps.inbox_hub.models import HubEmail

        support = _make_department(tenant, admin_user, slug="support")
        _add_to_department(tenant, support, agent_user)
        teammate = UserFactory()
        MembershipFactory(user=teammate, tenant=tenant)
        _add_to_department(tenant, support, teammate)

        new_one = _build_hub_email(tenant, department=support)
        in_flight = _build_hub_email(
            tenant, department=support, assignee=teammate,
            state=HubEmail.State.IN_PROGRESS,
        )

        ids = self._ids(agent_client.get("/api/v1/inbox-hub/hub-emails/"))
        assert str(new_one.id) in ids
        assert str(in_flight.id) not in ids

    def test_deactivated_department_mail_hidden(
        self, agent_client, agent_user, tenant, admin_user
    ):
        """Membership of a soft-disabled department surfaces none of its mail,
        even when the agent has Hub access via another active department."""
        sales = _make_department(tenant, admin_user, slug="sales")
        support = _make_department(tenant, admin_user, slug="support")
        _add_to_department(tenant, sales, agent_user)
        _add_to_department(tenant, support, agent_user)
        support.is_active = False
        support.save(update_fields=["is_active", "updated_at"])

        in_sales = _build_hub_email(tenant, department=sales)
        in_support = _build_hub_email(tenant, department=support)

        resp = agent_client.get("/api/v1/inbox-hub/hub-emails/")
        assert resp.status_code == 200
        ids = self._ids(resp)
        assert str(in_sales.id) in ids
        assert str(in_support.id) not in ids  # disabled dept's mail is hidden

    def test_manager_sees_all_departments(
        self, manager_client, tenant, admin_user
    ):
        sales = _make_department(tenant, admin_user, slug="sales")
        support = _make_department(tenant, admin_user, slug="support")
        a = _build_hub_email(tenant, department=sales)
        b = _build_hub_email(tenant, department=support)

        ids = self._ids(manager_client.get("/api/v1/inbox-hub/hub-emails/"))
        assert {str(a.id), str(b.id)} <= ids

    def test_agent_without_department_denied_when_departments_exist(
        self, agent_client, tenant, admin_user
    ):
        """Once a tenant has departments, an agent in none of them is locked
        out (no longer the no-department fall-open case)."""
        _make_department(tenant, admin_user, slug="sales")
        _build_hub_email(tenant, department=None)
        resp = agent_client.get("/api/v1/inbox-hub/hub-emails/")
        assert resp.status_code == 403
