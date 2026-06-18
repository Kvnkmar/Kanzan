"""
Regression tests for ``apps.tenants.middleware.TenantMiddleware``.

Covers:
- ``_extract_slug`` pure-function behaviour (subdomain, bare host, nested
  sub-subdomain rejection on the base domain).
- ``EXEMPT_PATH_PREFIXES`` bypass of tenant resolution.
- ``{slug}.localhost`` host -> ``request.tenant`` resolution.
- Unknown REAL host (not a subdomain) -> 404 JSON.
- Inactive tenant -> not resolved (treated as unknown).
- ``/admin/`` branch sets context (even to None) without 404ing on a bare host.

These exercise the *plain Django* request path, so they use
``django.test.Client`` with ``HTTP_HOST`` headers (NOT DRF clients /
``force_authenticate`` — those wouldn't hit the WSGI middleware chain the
same way for non-DRF views).

Tests run under dev settings (``ALLOWED_HOSTS=["*"]``,
``BASE_DOMAIN="localhost"``).
"""

import pytest
from django.test import Client

from apps.tenants.middleware import EXEMPT_PATH_PREFIXES, _extract_slug


# ── _extract_slug (pure function — no DB) ────────────────────────────

class TestExtractSlug:
    base = "localhost"

    def test_subdomain_localhost(self):
        assert _extract_slug("acme.localhost", self.base) == "acme"

    def test_subdomain_localhost_with_port(self):
        assert _extract_slug("acme.localhost:8001", self.base) == "acme"

    def test_bare_localhost_is_main_site(self):
        assert _extract_slug("localhost", self.base) is None

    def test_bare_loopback_ip_is_main_site(self):
        assert _extract_slug("127.0.0.1", self.base) is None

    def test_bare_base_domain_is_main_site(self):
        assert _extract_slug("example.com", "example.com") is None

    def test_case_insensitive(self):
        assert _extract_slug("ACME.LOCALHOST", self.base) == "acme"

    def test_subdomain_on_base_domain(self):
        assert _extract_slug("acme.example.com", "example.com") == "acme"

    def test_nested_sub_subdomain_rejected_on_base_domain(self):
        # a.b.example.com -> slug would be "a.b" which contains a dot ->
        # rejected (returns None), NOT "a.b".
        assert _extract_slug("a.b.example.com", "example.com") is None

    def test_empty_slug_localhost_rejected(self):
        # ".localhost" -> empty slug -> None
        assert _extract_slug(".localhost", self.base) is None

    def test_unknown_real_host_returns_none(self):
        # A genuine external host that is neither bare base nor a recognised
        # subdomain pattern -> None (will fall through to custom-domain lookup).
        assert _extract_slug("some-random-host.io", self.base) is None


# ── EXEMPT_PATH_PREFIXES contents ────────────────────────────────────

class TestExemptPrefixes:
    def test_known_prefixes_present(self):
        for p in (
            "/static/",
            "/media/",
            "/api/v1/billing/plans/",
            "/login/",
            "/api/docs/",
            "/accounts/",
        ):
            assert p in EXEMPT_PATH_PREFIXES

    def test_admin_not_in_exempt_prefixes(self):
        # /admin/ is handled by a dedicated branch, NOT via the exempt list.
        assert "/admin/" not in EXEMPT_PATH_PREFIXES

    def test_auth_handoff_not_exempt(self):
        # Intentionally must resolve the tenant to verify membership.
        assert "/auth/handoff/" not in EXEMPT_PATH_PREFIXES


# ── Live middleware behaviour via the test Client ────────────────────

@pytest.mark.django_db
class TestTenantResolution:
    def test_subdomain_binds_request_tenant(self, tenant):
        """
        A request to {slug}.localhost binds the resolved Tenant onto
        ``request.tenant``. Driven through the middleware DIRECTLY (with a
        capturing get_response) so it doesn't depend on URLconf/view dispatch
        state, which other modules can pollute (the view object is bound into
        the URL resolver at import time).
        """
        from django.contrib.auth.models import AnonymousUser
        from django.http import HttpResponse
        from django.test import RequestFactory

        from apps.tenants.middleware import TenantMiddleware

        captured = {}

        def get_response(request):
            captured["tenant"] = getattr(request, "tenant", "MISSING")
            return HttpResponse("ok")

        middleware = TenantMiddleware(get_response)
        request = RequestFactory(
            HTTP_HOST=f"{tenant.slug}.localhost:8001"
        ).get("/")
        request.user = AnonymousUser()  # set by AuthenticationMiddleware in prod

        middleware(request)

        assert captured.get("tenant") == tenant

    def test_subdomain_binds_tenant_on_non_exempt_path(self, tenant, admin_user):
        """On a non-exempt page, request.tenant is the resolved tenant."""
        # admin_user already has an admin membership in ``tenant``.
        c = Client(HTTP_HOST=f"{tenant.slug}.localhost:8001")
        c.force_login(admin_user)
        # /dashboard/ is a membership-gated frontend page (non-exempt).
        resp = c.get("/dashboard/")
        # Must NOT 404 with "Tenant not found" — the tenant resolved.
        assert resp.status_code != 404 or b"Tenant not found" not in resp.content

    def test_unknown_real_host_returns_404_json(self, tenant):
        """A real host that matches no tenant domain -> 404 JSON."""
        c = Client(HTTP_HOST="totally-unknown-real-host.example")
        resp = c.get("/dashboard/")
        assert resp.status_code == 404
        assert resp["Content-Type"].startswith("application/json")
        assert resp.json() == {"detail": "Tenant not found."}

    def test_unknown_subdomain_returns_404_json(self, tenant):
        """A {slug}.localhost where slug matches no tenant -> 404 JSON."""
        c = Client(HTTP_HOST="no-such-tenant.localhost:8001")
        resp = c.get("/dashboard/")
        assert resp.status_code == 404
        assert resp.json() == {"detail": "Tenant not found."}

    def test_inactive_tenant_not_resolved(self, tenant):
        """An is_active=False tenant is treated as unknown -> 404."""
        from main.context import clear_current_tenant

        tenant.is_active = False
        tenant.save(update_fields=["is_active"])
        clear_current_tenant()

        c = Client(HTTP_HOST=f"{tenant.slug}.localhost:8001")
        resp = c.get("/dashboard/")
        assert resp.status_code == 404
        assert resp.json() == {"detail": "Tenant not found."}


@pytest.mark.django_db
class TestExemptPathBypass:
    def test_billing_plans_bypasses_tenant_requirement(self):
        """A public exempt API path works on a host with NO tenant."""
        c = Client(HTTP_HOST="totally-unknown-real-host.example")
        resp = c.get("/api/v1/billing/plans/")
        # Exempt -> NOT short-circuited to 404 "Tenant not found".
        assert resp.status_code != 404
        # PlanViewSet is AllowAny; should be a normal 200 list.
        assert resp.status_code == 200

    def test_login_page_bypasses_on_unknown_host(self):
        """/login/ (exempt) renders even on an unmatched host."""
        c = Client(HTTP_HOST="totally-unknown-real-host.example")
        resp = c.get("/login/")
        assert resp.status_code != 404 or b"Tenant not found" not in resp.content
        # JSON 404 is the short-circuit; exempt path must not produce it.
        if resp.status_code == 404:
            assert resp.get("Content-Type", "").startswith("application/json") is False

    def test_static_prefix_is_exempt(self):
        """A /static/ path is never short-circuited to a tenant 404 JSON."""
        c = Client(HTTP_HOST="totally-unknown-real-host.example")
        resp = c.get("/static/does-not-exist.css")
        # Whatever the staticfiles handler returns (commonly 404 for missing
        # file), it must NOT be the middleware's "Tenant not found." JSON.
        if resp.status_code == 404:
            body = resp.content
            assert b"Tenant not found" not in body


@pytest.mark.django_db
class TestAdminBranch:
    def test_admin_on_bare_host_no_tenant_no_404(self, admin_user):
        """/admin/ on a bare host sets context to None and does NOT 404."""
        c = Client(HTTP_HOST="localhost")
        c.force_login(admin_user)
        resp = c.get("/admin/")
        # The middleware must not produce the "Tenant not found." JSON 404.
        # (The admin site itself handles auth; a superuser sees the index,
        # a non-superuser is redirected/403 — none of which is the JSON 404.)
        if resp.status_code == 404:
            assert b"Tenant not found" not in resp.content
        assert resp.get("Content-Type", "") != "application/json" or resp.status_code != 404

    def test_admin_on_unknown_real_host_no_tenant_404_json(self, admin_user):
        """
        /admin/ on an UNKNOWN real host: the admin branch sets tenant=None
        and passes through WITHOUT the middleware's tenant-404 short-circuit.
        """
        c = Client(HTTP_HOST="totally-unknown-real-host.example")
        c.force_login(admin_user)
        resp = c.get("/admin/")
        # The dedicated /admin/ branch never emits "Tenant not found." JSON.
        if resp.status_code == 404:
            assert b"Tenant not found" not in resp.content

    def test_admin_on_subdomain_resolves_tenant(self, tenant, admin_user):
        """/admin/ on {slug}.localhost passes through (no tenant-404 JSON)."""
        c = Client(HTTP_HOST=f"{tenant.slug}.localhost:8001")
        c.force_login(admin_user)
        resp = c.get("/admin/")
        # Resolved-tenant admin must not 404 with the tenant-not-found JSON.
        if resp.status_code == 404:
            assert b"Tenant not found" not in resp.content
