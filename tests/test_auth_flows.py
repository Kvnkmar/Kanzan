"""
Regression tests for the frontend auth flows (plain Django views, NOT DRF):

* ``apps/tenants/frontend_views.py``
    - ``verify_email_page`` — token activation, single-use / idempotent,
      expired / invalid handling.
    - handoff helpers ``_make_handoff_token`` / ``_read_handoff_token``
      round-trip + tamper / expiry rejection.
    - ``_safe_next_url`` open-redirect rejection.
    - ``auth_handoff`` view end-to-end (establishes a session on a tenant).
    - ``logout_page`` — bumps ``User.auth_version``.
* ``apps/accounts/middleware.py`` ``SessionVersionMiddleware`` — global logout
  invalidates other sessions whose stamped auth_version is stale.
* ``apps/accounts/models.py`` ``EmailVerificationToken`` — is_expired /
  is_consumed properties.

These are PLAIN Django views, so we use ``django.test.Client`` + ``force_login``
(DRF ``force_authenticate`` does not work for them).
"""

from datetime import timedelta

import pytest
from django.core import signing
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.tenants.frontend_views import (
    HANDOFF_TOKEN_TTL_SECONDS,
    SESSION_AUTH_VERSION_KEY,
    _make_handoff_token,
    _read_handoff_token,
    _safe_next_url,
)

# BASE_DOMAIN is "localhost" in the test settings, so the bare domain is just
# "localhost" and tenant subdomains are "<slug>.localhost".
BARE_HOST = "localhost:8001"


def _tenant_host(tenant):
    return f"{tenant.slug}.localhost:8001"


def _make_token(user, *, expires_in_hours=24, consumed=False):
    """Create an EmailVerificationToken row for *user*."""
    from apps.accounts.models import EmailVerificationToken
    import secrets

    token = EmailVerificationToken.objects.create(
        user=user,
        token=secrets.token_urlsafe(32),
        expires_at=timezone.now() + timedelta(hours=expires_in_hours),
        consumed_at=timezone.now() if consumed else None,
    )
    return token


# ---------------------------------------------------------------------------
# EmailVerificationToken model properties
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestEmailVerificationTokenModel:
    def test_fresh_token_not_expired_not_consumed(self, agent_user):
        token = _make_token(agent_user)
        assert token.is_expired is False
        assert token.is_consumed is False

    def test_past_expiry_is_expired(self, agent_user):
        token = _make_token(agent_user, expires_in_hours=-1)
        assert token.is_expired is True

    def test_consumed_flag_tracks_consumed_at(self, agent_user):
        token = _make_token(agent_user, consumed=True)
        assert token.is_consumed is True


# ---------------------------------------------------------------------------
# verify_email_page
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestVerifyEmailPage:
    """The verify-email page is served on the bare domain (no tenant)."""

    def _verify_url(self):
        return reverse("frontend:verify-email")

    def test_valid_token_activates_user(self, agent_user):
        # Deactivate first to simulate an unverified signup.
        agent_user.is_active = False
        agent_user.save(update_fields=["is_active"])
        token = _make_token(agent_user)

        c = Client(HTTP_HOST=BARE_HOST)
        resp = c.get(self._verify_url(), {"token": token.token})

        agent_user.refresh_from_db()
        token.refresh_from_db()
        # Activation happened.
        assert agent_user.is_active is True
        # Token is now consumed (single-use).
        assert token.is_consumed is True
        # User has a membership (agent_user) -> handoff redirect (3xx).
        assert resp.status_code in (302, 303)

    def test_missing_token_is_rejected(self):
        c = Client(HTTP_HOST=BARE_HOST)
        resp = c.get(self._verify_url())
        assert resp.status_code == 400

    def test_invalid_token_is_rejected(self):
        c = Client(HTTP_HOST=BARE_HOST)
        resp = c.get(self._verify_url(), {"token": "does-not-exist"})
        assert resp.status_code == 400

    def test_expired_token_does_not_activate(self, agent_user):
        agent_user.is_active = False
        agent_user.save(update_fields=["is_active"])
        token = _make_token(agent_user, expires_in_hours=-1)

        c = Client(HTTP_HOST=BARE_HOST)
        resp = c.get(self._verify_url(), {"token": token.token})

        agent_user.refresh_from_db()
        token.refresh_from_db()
        assert resp.status_code == 400
        # Stayed inactive; token not consumed.
        assert agent_user.is_active is False
        assert token.is_consumed is False

    def test_already_consumed_token_does_not_reactivate_inactive_user(self, agent_user):
        """A consumed token for a still-inactive user errors (does not reactivate)."""
        agent_user.is_active = False
        agent_user.save(update_fields=["is_active"])
        token = _make_token(agent_user, consumed=True)

        c = Client(HTTP_HOST=BARE_HOST)
        resp = c.get(self._verify_url(), {"token": token.token})

        agent_user.refresh_from_db()
        assert resp.status_code == 400
        assert agent_user.is_active is False

    def test_consume_is_single_use_idempotent(self, agent_user):
        """Re-clicking a consumed token for an already-active user logs them in (no error)."""
        # agent_user is active and already a member -> idempotent success path.
        token = _make_token(agent_user, consumed=True)

        c = Client(HTTP_HOST=BARE_HOST)
        resp = c.get(self._verify_url(), {"token": token.token})
        # Idempotent: log in + post-auth redirect, not a 400 error.
        assert resp.status_code in (302, 303)


# ---------------------------------------------------------------------------
# Handoff token helpers
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestHandoffToken:
    def test_make_read_round_trip(self, agent_user):
        token = _make_handoff_token(agent_user.id, "/tickets/")
        payload = _read_handoff_token(token)
        assert payload["uid"] == str(agent_user.id)
        assert payload["next"] == "/tickets/"

    def test_default_next_is_dashboard(self, agent_user):
        token = _make_handoff_token(agent_user.id)
        payload = _read_handoff_token(token)
        assert payload["next"] == "/dashboard/"

    def test_tampered_token_is_rejected(self, agent_user):
        token = _make_handoff_token(agent_user.id)
        tampered = token[:-3] + ("zzz" if not token.endswith("zzz") else "aaa")
        with pytest.raises(signing.BadSignature):
            _read_handoff_token(tampered)

    def test_wrong_salt_is_rejected(self, agent_user):
        # A token signed with a different salt must not validate.
        token = signing.dumps(
            {"uid": str(agent_user.id), "next": "/dashboard/"},
            salt="some.other.salt",
        )
        with pytest.raises(signing.BadSignature):
            _read_handoff_token(token)

    def test_expired_token_is_rejected(self, agent_user):
        # Craft a token timestamped older than the TTL by reusing the signer
        # machinery; freezegun-free by signing in the past.
        from freezegun import freeze_time

        past = timezone.now() - timedelta(seconds=HANDOFF_TOKEN_TTL_SECONDS + 30)
        with freeze_time(past):
            token = _make_handoff_token(agent_user.id)
        with pytest.raises(signing.SignatureExpired):
            _read_handoff_token(token)

    def test_ttl_is_short(self):
        assert HANDOFF_TOKEN_TTL_SECONDS == 30


# ---------------------------------------------------------------------------
# _safe_next_url open-redirect guard
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestSafeNextUrl:
    def test_external_url_rejected(self):
        assert _safe_next_url("https://evil.example.com/steal") is None

    def test_external_localhost_lookalike_rejected(self):
        # Not a subdomain of BASE_DOMAIN.
        assert _safe_next_url("https://localhost.evil.com/x") is None

    def test_relative_url_rejected(self):
        # Relative paths are rejected (no netloc) — bare-domain routing handles them.
        assert _safe_next_url("/dashboard/") is None

    def test_bare_domain_url_rejected(self):
        # URL on the bare BASE_DOMAIN itself is rejected.
        assert _safe_next_url("http://localhost:8001/dashboard/") is None

    def test_empty_rejected(self):
        assert _safe_next_url("") is None
        assert _safe_next_url(None) is None

    def test_tenant_subdomain_url_accepted(self):
        url = "http://acme.localhost:8001/tickets/"
        assert _safe_next_url(url) == url

    def test_protocol_relative_external_rejected(self):
        assert _safe_next_url("//evil.example.com/x") is None


# ---------------------------------------------------------------------------
# auth_handoff view — establishes a session on a tenant subdomain
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAuthHandoffView:
    def _url(self):
        return reverse("frontend:auth-handoff")

    def test_valid_token_logs_member_in_and_redirects(self, tenant, agent_user):
        token = _make_handoff_token(agent_user.id, "/tickets/")
        c = Client(HTTP_HOST=_tenant_host(tenant))
        resp = c.get(self._url(), {"t": token})
        assert resp.status_code in (302, 303)
        # Redirected to the requested in-tenant path.
        assert resp["Location"] == "/tickets/"
        # Session was established (auth_version stamped).
        assert c.session.get("_auth_user_id") == str(agent_user.id)
        assert c.session.get(SESSION_AUTH_VERSION_KEY) == agent_user.auth_version

    def test_missing_token_redirects_to_login(self, tenant):
        c = Client(HTTP_HOST=_tenant_host(tenant))
        resp = c.get(self._url())
        assert resp.status_code in (302, 303)
        assert "/login/" in resp["Location"]
        # No session established.
        assert c.session.get("_auth_user_id") is None

    def test_tampered_token_redirects_to_login(self, tenant, agent_user):
        token = _make_handoff_token(agent_user.id)
        tampered = token[:-3] + ("zzz" if not token.endswith("zzz") else "aaa")
        c = Client(HTTP_HOST=_tenant_host(tenant))
        resp = c.get(self._url(), {"t": tampered})
        assert resp.status_code in (302, 303)
        assert "/login/" in resp["Location"]
        assert c.session.get("_auth_user_id") is None

    def test_non_member_bounced_to_workspaces(self, tenant, tenant_b):
        # A user who is a member of tenant_b but NOT tenant should be bounced.
        from conftest import UserFactory, MembershipFactory

        outsider = UserFactory()
        MembershipFactory(tenant=tenant_b, user=outsider)
        token = _make_handoff_token(outsider.id)

        c = Client(HTTP_HOST=_tenant_host(tenant))
        resp = c.get(self._url(), {"t": token})
        assert resp.status_code in (302, 303)
        assert "/workspaces/" in resp["Location"]
        assert c.session.get("_auth_user_id") is None

    def test_open_redirect_next_is_neutralised(self, tenant, agent_user):
        # An absolute http next-path in the token must NOT be honoured.
        token = _make_handoff_token(agent_user.id, "https://evil.example.com/")
        c = Client(HTTP_HOST=_tenant_host(tenant))
        resp = c.get(self._url(), {"t": token})
        assert resp.status_code in (302, 303)
        # Falls back to the safe in-tenant dashboard.
        assert resp["Location"] == "/dashboard/"


# ---------------------------------------------------------------------------
# SessionVersionMiddleware — global logout
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestSessionVersionMiddleware:
    def _authed_client(self, tenant, user):
        """A Django Client with a real session that has stamped auth_version."""
        c = Client(HTTP_HOST=_tenant_host(tenant))
        c.force_login(user)
        # force_login does not stamp our custom version, so do it explicitly
        # to mirror the login views (otherwise the middleware would *adopt*
        # the version on the next request rather than test the revoke path).
        session = c.session
        session[SESSION_AUTH_VERSION_KEY] = user.auth_version
        session.save()
        return c

    def test_stamped_session_survives_normal_request(self, tenant, agent_user):
        c = self._authed_client(tenant, agent_user)
        resp = c.get(reverse("frontend:dashboard"))
        # Still authenticated (page renders, not bounced to login).
        assert resp.status_code == 200
        assert c.session.get("_auth_user_id") == str(agent_user.id)

    def test_bumping_auth_version_invalidates_existing_session(self, tenant, agent_user):
        from django.db.models import F

        c = self._authed_client(tenant, agent_user)
        # Sanity: works before bump.
        resp = c.get(reverse("frontend:dashboard"))
        assert resp.status_code == 200

        # Global logout elsewhere bumps the user's auth_version in the DB.
        agent_user.__class__.objects.filter(pk=agent_user.pk).update(
            auth_version=F("auth_version") + 1
        )

        # The next request on this (now-stale) session must be logged out.
        resp = c.get(reverse("frontend:dashboard"))
        # Logged out -> @login_required bounces to /login/.
        assert resp.status_code in (302, 303)
        assert "/login/" in resp["Location"]
        # Session no longer carries the user.
        assert c.session.get("_auth_user_id") is None

    def test_unstamped_session_is_adopted_not_revoked(self, tenant, agent_user):
        """A session without a stamped version (e.g. admin login) is adopted, not killed."""
        c = Client(HTTP_HOST=_tenant_host(tenant))
        c.force_login(agent_user)  # no auth_version stamp
        # First authenticated request: middleware adopts current version.
        resp = c.get(reverse("frontend:dashboard"))
        assert resp.status_code == 200
        assert c.session.get(SESSION_AUTH_VERSION_KEY) == agent_user.auth_version
        # Still logged in.
        assert c.session.get("_auth_user_id") == str(agent_user.id)


# ---------------------------------------------------------------------------
# logout_page — global logout bumps auth_version
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestLogoutPage:
    def test_logout_bumps_auth_version_and_clears_session(self, tenant, agent_user):
        before = agent_user.auth_version
        c = Client(HTTP_HOST=_tenant_host(tenant))
        c.force_login(agent_user)
        resp = c.get(reverse("frontend:logout"))

        agent_user.refresh_from_db()
        assert agent_user.auth_version == before + 1
        # Local session terminated.
        assert c.session.get("_auth_user_id") is None
        # Redirected to bare-domain login.
        assert resp.status_code in (302, 303)
        assert "/login/" in resp["Location"]

    def test_anonymous_logout_is_noop_redirect(self, tenant):
        c = Client(HTTP_HOST=_tenant_host(tenant))
        resp = c.get(reverse("frontend:logout"))
        assert resp.status_code in (302, 303)
        assert "/login/" in resp["Location"]
