"""
Tests for ``apps.api_keys.authentication.APIKeyAuthentication``.

Strategy:
- Mint a real key via ``apps.api_keys.services.create_api_key``.
- Build an unauthenticated DRF ``APIClient`` with the tenant subdomain Host
  header set.
- Use ``client.credentials(HTTP_AUTHORIZATION=f"Api-Key {cleartext}")`` to
  present the key.
- Hit ``GET /api/v1/tickets/tickets/`` (tenant-scoped, requires auth) and
  inspect the response.

All assertions go through DRF's HTTP cycle, so we end-to-end-exercise the
middleware stack, tenant resolution, auth class, permission classes, and the
``last_used_*`` / ``request_count`` side-effects.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.api_keys.services import create_api_key, revoke_api_key


pytestmark = pytest.mark.django_db


TICKETS_URL = "/api/v1/tickets/tickets/"
USERS_URL = "/api/v1/accounts/users/"


# ── Helpers ──────────────────────────────────────────────────────────


def _key_client(tenant) -> APIClient:
    """An unauthenticated APIClient with the tenant Host header set."""
    client = APIClient()
    client.defaults["HTTP_HOST"] = f"{tenant.slug}.localhost:8001"
    return client


def _mint(tenant, role, created_by, **kwargs):
    """Convenience wrapper around create_api_key returning (apikey, cleartext)."""
    return create_api_key(
        tenant=tenant,
        name=kwargs.pop("name", "Test Key"),
        role=role,
        created_by=created_by,
        **kwargs,
    )


# ── Tests ────────────────────────────────────────────────────────────


def test_valid_key_authenticates(tenant, admin_role, admin_user, default_status):
    """A freshly minted key returns 200 on a tenant-scoped GET."""
    _, cleartext = _mint(tenant, admin_role, admin_user)
    client = _key_client(tenant)
    client.credentials(HTTP_AUTHORIZATION=f"Api-Key {cleartext}")

    resp = client.get(TICKETS_URL)
    assert resp.status_code == status.HTTP_200_OK, resp.content


def test_invalid_key_returns_401(tenant, admin_user):
    """An unknown but well-formed key returns 401."""
    client = _key_client(tenant)
    client.credentials(HTTP_AUTHORIZATION="Api-Key kz_live_dpap_garbagebutnotrealkey")

    resp = client.get(TICKETS_URL)
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_missing_authorization_header_falls_through(tenant):
    """
    No Authorization header at all → 401 from IsAuthenticated, NOT from the
    Api-Key auth class. This proves the auth class returns ``None`` rather
    than raising when its scheme is absent.
    """
    client = _key_client(tenant)
    # No credentials() call — header is absent.
    resp = client.get(TICKETS_URL)
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_wrong_keyword_falls_through(tenant, admin_role, admin_user):
    """
    Authorization: Bearer <something> → APIKeyAuthentication returns None,
    JWT auth class then takes over. The request still ends up rejected (the
    Bearer token is bogus) — but the rejection is from JWT auth, not ours.
    The important assertion: the request is processed (not 500'd).
    """
    client = _key_client(tenant)
    client.credentials(HTTP_AUTHORIZATION="Bearer some.fake.jwt.token")

    resp = client.get(TICKETS_URL)
    # JWT will reject this — 401 is the expected outcome — but the *processing*
    # path is what we care about (we did not raise inside APIKeyAuthentication).
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_malformed_authorization_header_no_value_returns_401(tenant):
    """``Api-Key`` with no value → 401 with the standardised message."""
    client = _key_client(tenant)
    client.credentials(HTTP_AUTHORIZATION="Api-Key")

    resp = client.get(TICKETS_URL)
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_malformed_authorization_header_too_many_parts_returns_401(tenant):
    """``Api-Key foo bar baz`` (>2 parts) → 401."""
    client = _key_client(tenant)
    client.credentials(HTTP_AUTHORIZATION="Api-Key foo bar baz")

    resp = client.get(TICKETS_URL)
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED
    # Best-effort assertion on the error body — DRF wraps the message
    # in {"detail": ...}.
    body = resp.json() if resp.content else {}
    assert "Invalid Authorization header format" in str(body)


def test_key_without_kz_live_prefix_returns_401(tenant):
    """A non-CRM-format token (no ``kz_live_`` prefix) → 401."""
    client = _key_client(tenant)
    client.credentials(HTTP_AUTHORIZATION="Api-Key not-crm-format")

    resp = client.get(TICKETS_URL)
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_revoked_key_returns_401(tenant, admin_role, admin_user, default_status):
    """A key that has been soft-revoked returns 401 on subsequent use."""
    apikey, cleartext = _mint(tenant, admin_role, admin_user)
    client = _key_client(tenant)
    client.credentials(HTTP_AUTHORIZATION=f"Api-Key {cleartext}")

    # Sanity: works before revoke.
    pre = client.get(TICKETS_URL)
    assert pre.status_code == status.HTTP_200_OK

    revoke_api_key(apikey, by=admin_user)

    post = client.get(TICKETS_URL)
    assert post.status_code == status.HTTP_401_UNAUTHORIZED


def test_expired_key_returns_401(tenant, admin_role, admin_user):
    """A key whose expires_at is in the past returns 401."""
    _, cleartext = _mint(
        tenant,
        admin_role,
        admin_user,
        expires_at=timezone.now() - timedelta(minutes=1),
    )
    client = _key_client(tenant)
    client.credentials(HTTP_AUTHORIZATION=f"Api-Key {cleartext}")

    resp = client.get(TICKETS_URL)
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_cross_tenant_key_returns_401(tenant, tenant_b, admin_role, admin_user):
    """
    A key minted on tenant A, presented against tenant B's subdomain → 401.

    Uses the ``cross-tenant guard`` branch in APIKeyAuthentication, which
    rejects when ``request.tenant != key.tenant``.
    """
    _, cleartext = _mint(tenant, admin_role, admin_user)

    # Same cleartext, but target tenant B's subdomain.
    client = APIClient()
    client.defaults["HTTP_HOST"] = f"{tenant_b.slug}.localhost:8001"
    client.credentials(HTTP_AUTHORIZATION=f"Api-Key {cleartext}")

    resp = client.get(TICKETS_URL)
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_last_used_at_updates_on_success(tenant, admin_role, admin_user, default_status):
    """First successful auth sets last_used_at + request_count."""
    apikey, cleartext = _mint(tenant, admin_role, admin_user)
    assert apikey.last_used_at is None
    assert apikey.request_count == 0

    client = _key_client(tenant)
    client.credentials(HTTP_AUTHORIZATION=f"Api-Key {cleartext}")
    resp = client.get(TICKETS_URL)
    assert resp.status_code == status.HTTP_200_OK

    apikey.refresh_from_db()
    assert apikey.last_used_at is not None
    assert apikey.request_count == 1


def test_last_used_at_does_not_update_on_failure(tenant, admin_role, admin_user):
    """
    A request with a tampered cleartext (wrong hash) fails auth → 401, and
    ``last_used_at`` must remain None.
    """
    apikey, cleartext = _mint(tenant, admin_role, admin_user)
    assert apikey.last_used_at is None

    # Same prefix, different secret body → bypasses the prefix lookup but
    # fails the hash compare.
    tampered = cleartext[:20] + "_TAMPERED_TAMPERED_TAMPERED_TAMPERED_TAMPERED"
    client = _key_client(tenant)
    client.credentials(HTTP_AUTHORIZATION=f"Api-Key {tampered}")

    resp = client.get(TICKETS_URL)
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    apikey.refresh_from_db()
    assert apikey.last_used_at is None
    assert apikey.request_count == 0


def test_request_count_increments(tenant, admin_role, admin_user, default_status):
    """Three successful requests → request_count == 3."""
    apikey, cleartext = _mint(tenant, admin_role, admin_user)
    client = _key_client(tenant)
    client.credentials(HTTP_AUTHORIZATION=f"Api-Key {cleartext}")

    for _ in range(3):
        resp = client.get(TICKETS_URL)
        assert resp.status_code == status.HTTP_200_OK

    apikey.refresh_from_db()
    assert apikey.request_count == 3


@pytest.mark.xfail(
    reason=(
        "Service-account filtering on UserViewSet.get_queryset is not yet "
        "implemented; tracked in plan A2 follow-up. The hidden service_user "
        "should be excluded from /api/v1/accounts/users/ list responses."
    ),
    strict=False,
)
def test_service_user_not_in_users_list(tenant, admin_role, admin_user, admin_client):
    """
    After minting a key, the synthetic service_user must NOT appear in the
    users list. This is a visibility safety net for tenant admins.

    NOTE: at the time of writing, ``apps.accounts.views.UserViewSet.get_queryset``
    does not filter ``is_service_account=True``, so this is xfail.
    """
    _mint(tenant, admin_role, admin_user)

    resp = admin_client.get(USERS_URL)
    assert resp.status_code == status.HTTP_200_OK

    data = resp.json()
    results = data.get("results", data) if isinstance(data, dict) else data
    emails = [u.get("email", "") for u in results]
    # Every service user has an email starting with "service+".
    assert not any(e.startswith("service+") for e in emails), (
        f"Service-account user leaked into users list: {emails}"
    )


def test_inactive_service_user_blocks_auth(tenant, admin_role, admin_user, default_status):
    """
    If the underlying service_user is deactivated (without going through
    revoke_api_key), auth must still reject the key.
    """
    apikey, cleartext = _mint(tenant, admin_role, admin_user)

    # Sanity: works before deactivating the service_user.
    client = _key_client(tenant)
    client.credentials(HTTP_AUTHORIZATION=f"Api-Key {cleartext}")
    pre = client.get(TICKETS_URL)
    assert pre.status_code == status.HTTP_200_OK

    # Deactivate the service_user out-of-band.
    User = get_user_model()
    User.objects.filter(pk=apikey.service_user_id).update(is_active=False)

    post = client.get(TICKETS_URL)
    assert post.status_code == status.HTTP_401_UNAUTHORIZED
