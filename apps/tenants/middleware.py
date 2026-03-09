"""
Tenant resolution middleware for subdomain and custom-domain multi-tenancy.

Must be placed AFTER ``AuthenticationMiddleware`` in the ``MIDDLEWARE`` list
so that ``request.user`` is available.

Resolution strategy:
    1. ``<slug>.localhost``  -> look up Tenant by ``slug``
    2. ``<slug>.<BASE_DOMAIN>`` -> look up Tenant by ``slug``
    3. Any other host        -> look up Tenant by ``domain`` field (custom domain)
    4. Bare ``localhost`` / ``BASE_DOMAIN`` -> main site (``request.tenant = None``)
"""

import json
import logging

from channels.db import database_sync_to_async
from django.conf import settings
from django.http import JsonResponse

from main.context import clear_current_tenant, set_current_tenant

logger = logging.getLogger(__name__)

# Paths that are always served regardless of tenant resolution.
EXEMPT_PATH_PREFIXES = (
    "/admin/",
    "/static/",
    "/media/",
    "/api/v1/accounts/auth/",
    "/api/v1/billing/plans/",
    "/api/v1/billing/webhook/",
    "/api/docs/",
    "/api/schema/",
    "/accounts/",
)


class TenantMiddleware:
    """
    Resolve the current tenant from the request host and inject it into
    ``request.tenant`` and the thread-local context used by
    ``TenantAwareManager``.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.base_domain = getattr(settings, "BASE_DOMAIN", "localhost")

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_exempt(path: str) -> bool:
        """Return True if *path* should bypass tenant resolution."""
        return any(path.startswith(prefix) for prefix in EXEMPT_PATH_PREFIXES)

    def _extract_slug(self, host: str) -> str | None:
        """
        Return the tenant slug from *host*, or ``None`` if the host is the
        bare domain (main site) or not a recognised subdomain pattern.
        """
        # Strip port number if present.
        host = host.split(":")[0].lower()

        # Bare localhost or bare BASE_DOMAIN -> main site.
        if host in ("localhost", "127.0.0.1", self.base_domain):
            return None

        # <slug>.localhost
        if host.endswith(".localhost"):
            slug = host.removesuffix(".localhost")
            return slug if slug else None

        # <slug>.<BASE_DOMAIN>
        suffix = f".{self.base_domain}"
        if host.endswith(suffix):
            slug = host.removesuffix(suffix)
            # Guard against nested sub-subdomains (e.g. a.b.example.com).
            if slug and "." not in slug:
                return slug

        return None

    # ------------------------------------------------------------------
    # main entry point
    # ------------------------------------------------------------------

    def __call__(self, request):
        # Lazy import to avoid circular import at module level.
        from apps.tenants.models import Tenant

        path = request.path

        # --- Exempt paths ---
        if self._is_exempt(path):
            request.tenant = None
            set_current_tenant(None)
            response = self.get_response(request)
            clear_current_tenant()
            return response

        # --- Resolve tenant ---
        host = request.get_host()
        host_bare = host.split(":")[0].lower()
        slug = self._extract_slug(host)

        tenant = None

        if slug is not None:
            # Subdomain-based lookup.
            tenant = (
                Tenant.objects.filter(slug=slug, is_active=True)
                .select_related("settings")
                .first()
            )
        elif host_bare in ("localhost", "127.0.0.1", self.base_domain):
            # Main site – no tenant required.
            request.tenant = None
            set_current_tenant(None)
            response = self.get_response(request)
            clear_current_tenant()
            return response
        else:
            # Custom domain lookup.
            tenant = (
                Tenant.objects.filter(domain=host_bare, is_active=True)
                .select_related("settings")
                .first()
            )

        if tenant is None:
            logger.warning("Tenant not found for host %s (slug=%s)", host, slug)
            return JsonResponse(
                {"detail": "Tenant not found."},
                status=404,
            )

        # Inject tenant into the request and thread-local context.
        request.tenant = tenant
        set_current_tenant(tenant)

        try:
            response = self.get_response(request)
        finally:
            clear_current_tenant()

        return response


# ---------------------------------------------------------------------------
# WebSocket tenant middleware (ASGI)
# ---------------------------------------------------------------------------


class WebSocketTenantMiddleware:
    """
    ASGI middleware that resolves the tenant from the WebSocket connection's
    ``Host`` header and sets ``scope["tenant"]`` + the context-local tenant.

    Place this between ``AuthMiddlewareStack`` and the ``URLRouter``::

        AuthMiddlewareStack(WebSocketTenantMiddleware(URLRouter(...)))
    """

    def __init__(self, app):
        self.app = app
        self.base_domain = getattr(settings, "BASE_DOMAIN", "localhost")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "websocket":
            return await self.app(scope, receive, send)

        headers = dict(scope.get("headers", []))
        host = headers.get(b"host", b"").decode("utf-8", errors="replace")
        host_bare = host.split(":")[0].lower()

        tenant = None
        slug = self._extract_slug(host)

        if slug is not None:
            tenant = await self._get_tenant_by_slug(slug)
        elif host_bare not in ("localhost", "127.0.0.1", self.base_domain):
            tenant = await self._get_tenant_by_domain(host_bare)

        scope["tenant"] = tenant
        if tenant:
            set_current_tenant(tenant)

        try:
            return await self.app(scope, receive, send)
        finally:
            clear_current_tenant()

    def _extract_slug(self, host: str) -> str | None:
        """Return the tenant slug from *host*, or None."""
        host = host.split(":")[0].lower()
        if host in ("localhost", "127.0.0.1", self.base_domain):
            return None
        if host.endswith(".localhost"):
            slug = host.removesuffix(".localhost")
            return slug if slug else None
        suffix = f".{self.base_domain}"
        if host.endswith(suffix):
            slug = host.removesuffix(suffix)
            if slug and "." not in slug:
                return slug
        return None

    @database_sync_to_async
    def _get_tenant_by_slug(self, slug):
        from apps.tenants.models import Tenant

        return (
            Tenant.objects.filter(slug=slug, is_active=True)
            .select_related("settings")
            .first()
        )

    @database_sync_to_async
    def _get_tenant_by_domain(self, domain):
        from apps.tenants.models import Tenant

        return (
            Tenant.objects.filter(domain=domain, is_active=True)
            .select_related("settings")
            .first()
        )
