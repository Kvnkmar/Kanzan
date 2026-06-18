"""Regression tests for attachment object-level authorization
(attachments-1/2/3). Previously any tenant member could read/download/upload
attachments on ANY object in the tenant; now access is gated by the target
object's own visibility rules (agents only their own tickets, etc.)."""

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.attachments.access import can_access_target
from apps.attachments.models import Attachment
from main.context import tenant_context

from conftest import MembershipFactory, TicketFactory, make_api_client


def _ticket_ct():
    return ContentType.objects.get(app_label="tickets", model="ticket")


@pytest.mark.django_db
class TestCanAccessTarget:
    def test_ticket_visibility_rules(
        self, tenant, default_status, agent_user, admin_user, agent_role
    ):
        owner = MembershipFactory(tenant=tenant, role=agent_role).user
        ticket = TicketFactory(
            tenant=tenant, status=default_status, created_by=admin_user, assignee=owner
        )
        ct = _ticket_ct()
        # Another agent: denied. Assignee: allowed. Admin: bypass.
        # can_access_target resolves the target via the tenant-scoped manager,
        # so it runs within bound tenant context (as it does behind the API).
        with tenant_context(tenant):
            assert can_access_target(agent_user, tenant, ct, ticket.id) is False
            assert can_access_target(owner, tenant, ct, ticket.id) is True
            assert can_access_target(admin_user, tenant, ct, ticket.id) is True


@pytest.mark.django_db
class TestAttachmentApiAuthz:
    def _attachment_on(self, tenant, ticket, uploader):
        with tenant_context(tenant):
            return Attachment.objects.create(
                tenant=tenant,
                content_type=_ticket_ct(),
                object_id=ticket.id,
                file=SimpleUploadedFile("x.txt", b"hello"),
                original_name="x.txt",
                mime_type="text/plain",
                size_bytes=5,
                uploaded_by=uploader,
            )

    def test_agent_cannot_retrieve_or_download_unowned_attachment(
        self, tenant, default_status, agent_user, admin_user, agent_role
    ):
        owner = MembershipFactory(tenant=tenant, role=agent_role).user
        ticket = TicketFactory(
            tenant=tenant, status=default_status, created_by=admin_user, assignee=owner
        )
        att = self._attachment_on(tenant, ticket, admin_user)

        intruder = make_api_client(agent_user, tenant)
        base = f"/api/v1/attachments/attachments/{att.id}/"
        assert intruder.get(base).status_code == 403
        assert intruder.get(base + "download/").status_code == 403

        # The ticket's assignee can.
        owner_client = make_api_client(owner, tenant)
        assert owner_client.get(base).status_code == 200

    def test_agent_cannot_upload_to_unowned_ticket(
        self, tenant, default_status, agent_user, admin_user, agent_role
    ):
        owner = MembershipFactory(tenant=tenant, role=agent_role).user
        ticket = TicketFactory(
            tenant=tenant, status=default_status, created_by=admin_user, assignee=owner
        )
        client = make_api_client(agent_user, tenant)
        resp = client.post(
            "/api/v1/attachments/attachments/",
            {
                "content_type": "tickets.ticket",
                "object_id": str(ticket.id),
                "file": SimpleUploadedFile("x.txt", b"hello"),
            },
            format="multipart",
        )
        assert resp.status_code == 403
        assert Attachment.unscoped.filter(object_id=ticket.id).count() == 0
