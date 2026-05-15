"""
Tests for ``apps.api_keys.views.APIKeyViewSet`` — the CRUD + custom-action
management surface for tenant admins.

Covers:
- Admin-only access (IsTenantAdmin) — managers/agents/viewers/anon all blocked.
- ``create`` returns the cleartext exactly once; ``retrieve`` never includes it.
- Default role = Admin; explicit role_id honoured (cross-tenant role rejected).
- Duplicate name → 400 (unique-together).
- ``regenerate`` rotates the cleartext and invalidates the old one.
- ``revoke`` flips is_active and breaks subsequent auth attempts.
- ``destroy`` hard-deletes the row.
- Cross-tenant admins cannot see another tenant's keys.
- Role inheritance: a key whose role is Agent inherits the row-level
  IsTicketAccessible filter — proves HasTenantPermission + effective_role wire
  together for keys.
- Audit-log rows written on create / regenerate / revoke.
- Email task queued on create (via celery_eager → filebased email backend OR
  monkeypatch).
"""

from __future__ import annotations

import pytest
from django.contrib.contenttypes.models import ContentType
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import Role
from apps.api_keys.models import APIKey
from apps.api_keys.services import create_api_key
from apps.comments.models import ActivityLog


pytestmark = pytest.mark.django_db


API_KEYS_URL = "/api/v1/api-keys/"
TICKETS_URL = "/api/v1/tickets/tickets/"


def _key_client(tenant) -> APIClient:
    """Unauthenticated APIClient with tenant Host header — for key auth."""
    client = APIClient()
    client.defaults["HTTP_HOST"] = f"{tenant.slug}.localhost:8001"
    return client


# ── Listing / Reading ────────────────────────────────────────────────


def test_admin_can_list_keys(admin_client):
    """Empty list initially → 200 with empty results."""
    resp = admin_client.get(API_KEYS_URL)
    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    # Paginated payload.
    assert body.get("count", len(body.get("results", []))) == 0


# ── Creation ─────────────────────────────────────────────────────────


def test_admin_can_create_key_and_receives_cleartext_once(admin_client, admin_role):
    """POST → 201 with cleartext in `key`; subsequent GET omits `key`."""
    resp = admin_client.post(
        API_KEYS_URL,
        {"name": "Zapier Integration", "role_id": str(admin_role.pk)},
        format="json",
    )
    assert resp.status_code == status.HTTP_201_CREATED, resp.content

    payload = resp.json()
    assert "key" in payload, "Cleartext should be returned on create."
    assert payload["key"].startswith("kz_live_")
    key_id = payload["id"]

    # Retrieve must NOT include cleartext.
    detail = admin_client.get(f"{API_KEYS_URL}{key_id}/")
    assert detail.status_code == status.HTTP_200_OK
    assert "key" not in detail.json(), "Cleartext leaked on retrieve!"


def test_create_with_no_role_defaults_to_admin_role(admin_client, tenant):
    """POST with only `name` → defaults to the Admin role."""
    resp = admin_client.post(API_KEYS_URL, {"name": "Default Role Key"}, format="json")
    assert resp.status_code == status.HTTP_201_CREATED, resp.content

    apikey = APIKey.unscoped.get(pk=resp.json()["id"])
    assert apikey.role.slug == "admin"


def test_create_with_invalid_role_from_other_tenant_returns_400(
    admin_client, tenant_b
):
    """role_id pointing at a role from a different tenant → 400."""
    role_b = Role.unscoped.get(tenant=tenant_b, slug="admin")
    resp = admin_client.post(
        API_KEYS_URL,
        {"name": "Bad role", "role_id": str(role_b.pk)},
        format="json",
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST


def test_create_with_duplicate_name_returns_400(admin_client, admin_role):
    """Same name twice → second 400 with a unique-name error."""
    first = admin_client.post(
        API_KEYS_URL,
        {"name": "Duplicate", "role_id": str(admin_role.pk)},
        format="json",
    )
    assert first.status_code == status.HTTP_201_CREATED

    second = admin_client.post(
        API_KEYS_URL,
        {"name": "Duplicate", "role_id": str(admin_role.pk)},
        format="json",
    )
    assert second.status_code == status.HTTP_400_BAD_REQUEST
    assert "name" in second.json()


# ── Regenerate ───────────────────────────────────────────────────────


def test_admin_can_regenerate_key(
    admin_client, admin_role, admin_user, tenant, default_status
):
    """
    POST /api-keys/{id}/regenerate/ → 200 with NEW cleartext.
    Old cleartext now 401s on /tickets/; new cleartext works.
    """
    apikey, old_cleartext = create_api_key(
        tenant=tenant, name="Rotateme", role=admin_role, created_by=admin_user,
    )

    # Sanity: old cleartext works.
    pre_client = _key_client(tenant)
    pre_client.credentials(HTTP_AUTHORIZATION=f"Api-Key {old_cleartext}")
    assert pre_client.get(TICKETS_URL).status_code == status.HTTP_200_OK

    # Rotate via the API.
    resp = admin_client.post(f"{API_KEYS_URL}{apikey.pk}/regenerate/")
    assert resp.status_code == status.HTTP_200_OK, resp.content
    new_cleartext = resp.json()["key"]
    assert new_cleartext != old_cleartext
    assert new_cleartext.startswith("kz_live_")

    # Old cleartext is now invalid.
    old_resp = pre_client.get(TICKETS_URL)
    assert old_resp.status_code == status.HTTP_401_UNAUTHORIZED

    # New cleartext works.
    new_client = _key_client(tenant)
    new_client.credentials(HTTP_AUTHORIZATION=f"Api-Key {new_cleartext}")
    assert new_client.get(TICKETS_URL).status_code == status.HTTP_200_OK


# ── Revoke ───────────────────────────────────────────────────────────


def test_admin_can_revoke_key(
    admin_client, admin_role, admin_user, tenant, default_status
):
    """POST /api-keys/{id}/revoke/ → 200, key 401s thereafter."""
    apikey, cleartext = create_api_key(
        tenant=tenant, name="Revokeme", role=admin_role, created_by=admin_user,
    )

    resp = admin_client.post(f"{API_KEYS_URL}{apikey.pk}/revoke/")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["is_active"] is False

    # The cleartext now 401s.
    key_client = _key_client(tenant)
    key_client.credentials(HTTP_AUTHORIZATION=f"Api-Key {cleartext}")
    auth_resp = key_client.get(TICKETS_URL)
    assert auth_resp.status_code == status.HTTP_401_UNAUTHORIZED


# ── Destroy ──────────────────────────────────────────────────────────


def test_admin_can_hard_delete_key(admin_client, admin_role, admin_user, tenant):
    """DELETE → 204; subsequent GET on that id → 404."""
    apikey, _ = create_api_key(
        tenant=tenant, name="Deleteme", role=admin_role, created_by=admin_user,
    )

    resp = admin_client.delete(f"{API_KEYS_URL}{apikey.pk}/")
    assert resp.status_code == status.HTTP_204_NO_CONTENT

    miss = admin_client.get(f"{API_KEYS_URL}{apikey.pk}/")
    assert miss.status_code == status.HTTP_404_NOT_FOUND


# ── Permission gating ────────────────────────────────────────────────


def test_manager_cannot_create_key(manager_client, admin_role):
    resp = manager_client.post(
        API_KEYS_URL,
        {"name": "Manager forbidden", "role_id": str(admin_role.pk)},
        format="json",
    )
    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_manager_cannot_list_keys(manager_client):
    """IsTenantAdmin is strict — managers cannot even read."""
    resp = manager_client.get(API_KEYS_URL)
    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_agent_cannot_create_or_list_keys(agent_client, admin_role):
    list_resp = agent_client.get(API_KEYS_URL)
    assert list_resp.status_code == status.HTTP_403_FORBIDDEN

    create_resp = agent_client.post(
        API_KEYS_URL,
        {"name": "Agent forbidden", "role_id": str(admin_role.pk)},
        format="json",
    )
    assert create_resp.status_code == status.HTTP_403_FORBIDDEN


def test_viewer_cannot_create_or_list_keys(viewer_client, admin_role):
    list_resp = viewer_client.get(API_KEYS_URL)
    assert list_resp.status_code == status.HTTP_403_FORBIDDEN

    create_resp = viewer_client.post(
        API_KEYS_URL,
        {"name": "Viewer forbidden", "role_id": str(admin_role.pk)},
        format="json",
    )
    assert create_resp.status_code == status.HTTP_403_FORBIDDEN


def test_anon_cannot_access(anon_client, admin_role):
    list_resp = anon_client.get(API_KEYS_URL)
    assert list_resp.status_code == status.HTTP_401_UNAUTHORIZED

    create_resp = anon_client.post(
        API_KEYS_URL,
        {"name": "Anon forbidden", "role_id": str(admin_role.pk)},
        format="json",
    )
    assert create_resp.status_code == status.HTTP_401_UNAUTHORIZED


# ── Cross-tenant isolation ───────────────────────────────────────────


def test_cross_tenant_admin_cannot_see_other_tenant_keys(
    admin_client, tenant_b, admin_user
):
    """
    Tenant A's admin must not see tenant B's keys. We mint a key on tenant B
    via the service layer, then call /api-keys/ as tenant A's admin.
    """
    role_b = Role.unscoped.get(tenant=tenant_b, slug="admin")
    create_api_key(
        tenant=tenant_b, name="Tenant B key", role=role_b, created_by=admin_user,
    )

    resp = admin_client.get(API_KEYS_URL)
    assert resp.status_code == status.HTTP_200_OK
    results = resp.json().get("results", [])
    names = [k["name"] for k in results]
    assert "Tenant B key" not in names


# ── Role inheritance proof ───────────────────────────────────────────


def test_role_inheritance_agent_role_key_cannot_see_others_tickets(
    tenant, agent_role, admin_user, default_status
):
    """
    Mint a key with the Agent role and verify the IsTicketAccessible row
    filter applies — the agent-role key should only see tickets it created
    or is assigned to, not other admins' tickets.

    This proves the chain: APIKeyAuthentication → service_user →
    TenantMembership(role=Agent) → effective_role.hierarchy_level=30 →
    IsTicketAccessible row filter applied to the ticket queryset.
    """
    from apps.tickets.models import Ticket

    # Mint the agent-role key.
    apikey, cleartext = create_api_key(
        tenant=tenant, name="Reporting bot", role=agent_role, created_by=admin_user,
    )

    # Create a ticket owned by the human admin (NOT the service user).
    from main.context import set_current_tenant, clear_current_tenant

    set_current_tenant(tenant)
    try:
        # ``number`` is auto-assigned by Ticket.save() via TicketCounter.
        Ticket.objects.create(
            subject="Admin-owned ticket",
            description="Confidential",
            status=default_status,
            created_by=admin_user,
        )
    finally:
        clear_current_tenant()

    # The agent-role key lists tickets — should see NONE because:
    # service_user is not the creator and not the assignee.
    client = _key_client(tenant)
    client.credentials(HTTP_AUTHORIZATION=f"Api-Key {cleartext}")

    resp = client.get(TICKETS_URL)
    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    results = body.get("results", body) if isinstance(body, dict) else body
    assert len(results) == 0, (
        f"Agent-role key leaked admin's ticket(s): {results}"
    )


# ── Audit-log writes ─────────────────────────────────────────────────


def _activity_log_count(action: str, tenant) -> int:
    ct = ContentType.objects.get_for_model(APIKey)
    return ActivityLog.unscoped.filter(
        tenant=tenant, action=action, content_type=ct,
    ).count()


def test_activity_log_written_on_create(admin_client, admin_role, admin_user, tenant):
    before = _activity_log_count(ActivityLog.Action.API_KEY_CREATED, tenant)
    resp = admin_client.post(
        API_KEYS_URL,
        {"name": "Audit Test Create", "role_id": str(admin_role.pk)},
        format="json",
    )
    assert resp.status_code == status.HTTP_201_CREATED
    after = _activity_log_count(ActivityLog.Action.API_KEY_CREATED, tenant)
    assert after == before + 1

    # Actor should be the human admin.
    ct = ContentType.objects.get_for_model(APIKey)
    log = ActivityLog.unscoped.filter(
        tenant=tenant,
        action=ActivityLog.Action.API_KEY_CREATED,
        content_type=ct,
        object_id=resp.json()["id"],
    ).first()
    assert log is not None
    assert log.actor_id == admin_user.pk


def test_activity_log_written_on_regenerate(
    admin_client, admin_role, admin_user, tenant
):
    apikey, _ = create_api_key(
        tenant=tenant, name="Audit Regen", role=admin_role, created_by=admin_user,
    )
    before = _activity_log_count(ActivityLog.Action.API_KEY_REGENERATED, tenant)

    resp = admin_client.post(f"{API_KEYS_URL}{apikey.pk}/regenerate/")
    assert resp.status_code == status.HTTP_200_OK

    after = _activity_log_count(ActivityLog.Action.API_KEY_REGENERATED, tenant)
    assert after == before + 1


def test_activity_log_written_on_revoke(
    admin_client, admin_role, admin_user, tenant
):
    apikey, _ = create_api_key(
        tenant=tenant, name="Audit Revoke", role=admin_role, created_by=admin_user,
    )
    before = _activity_log_count(ActivityLog.Action.API_KEY_REVOKED, tenant)

    resp = admin_client.post(f"{API_KEYS_URL}{apikey.pk}/revoke/")
    assert resp.status_code == status.HTTP_200_OK

    after = _activity_log_count(ActivityLog.Action.API_KEY_REVOKED, tenant)
    assert after == before + 1


# ── Email task wiring ────────────────────────────────────────────────


def test_email_task_queued_on_create(
    admin_client, admin_role, admin_user, tenant, monkeypatch
):
    """
    On successful create, the ``send_api_key_created_email_task`` Celery task
    must be enqueued (here: invoked synchronously thanks to celery_eager).

    We monkey-patch ``.delay`` on the task to capture invocation rather than
    relying on the file-based email backend writing to disk — keeps the test
    hermetic and fast.
    """
    calls = []

    from apps.api_keys import tasks as api_key_tasks

    real_delay = api_key_tasks.send_api_key_created_email_task.delay

    def fake_delay(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        # Don't actually execute — we just want to verify it was enqueued.
        return None

    monkeypatch.setattr(
        api_key_tasks.send_api_key_created_email_task, "delay", fake_delay,
    )

    resp = admin_client.post(
        API_KEYS_URL,
        {"name": "Email Task Test", "role_id": str(admin_role.pk)},
        format="json",
    )
    assert resp.status_code == status.HTTP_201_CREATED, resp.content

    # transaction.on_commit fires after the surrounding atomic block exits.
    assert len(calls) == 1, f"Expected 1 task call, got {len(calls)}: {calls}"
    args = calls[0]["args"]
    # First arg: APIKey id (stringified); second arg: recipient email.
    assert str(args[0]) == resp.json()["id"]
    assert args[1] == admin_user.email
