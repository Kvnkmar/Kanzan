"""
DRF authentication backend for API keys.

Reads ``Authorization: Api-Key <cleartext>``, looks up the matching APIKey
row by indexed prefix, verifies the SHA-512 hash with a timing-safe compare,
and returns ``(service_user, apikey)`` so ``request.user`` becomes the
synthetic service-account user and ``request.auth`` becomes the APIKey row.

A best-effort ``last_used_*`` + ``request_count`` update runs on every
successful authentication via ``.update()`` (no signals, no race risk).
"""

from __future__ import annotations

import hashlib
import secrets

from django.db.models import F
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

from apps.api_keys.models import APIKey


class APIKeyAuthentication(BaseAuthentication):
    """
    DRF authentication using ``Authorization: Api-Key <kz_live_...>``.

    Returns ``None`` when the header is absent or uses a different scheme,
    so other authentication classes (JWT, Session) can still try. Raises
    ``AuthenticationFailed`` (401) only when the caller clearly *attempted*
    Api-Key auth but presented an invalid / revoked / expired / cross-tenant
    key — failing closed on attempted use.
    """

    keyword = "Api-Key"

    def authenticate(self, request):
        auth = get_authorization_header(request).split()
        if not auth or auth[0].lower() != self.keyword.lower().encode():
            # Not our scheme — let the next auth class try.
            return None
        if len(auth) != 2:
            raise AuthenticationFailed("Invalid Authorization header format")

        cleartext = auth[1].decode()
        if not cleartext.startswith("kz_live_"):
            raise AuthenticationFailed("Invalid API key format")

        prefix = cleartext[:20]
        try:
            key = APIKey.unscoped.select_related(
                "service_user", "role", "tenant"
            ).get(prefix=prefix)
        except APIKey.DoesNotExist:
            raise AuthenticationFailed("Invalid API key")

        if not key.is_active or not key.service_user.is_active:
            raise AuthenticationFailed("API key revoked")

        if key.expires_at and key.expires_at < timezone.now():
            raise AuthenticationFailed("API key expired")

        # Timing-safe hash comparison.
        candidate_hash = hashlib.sha512(cleartext.encode()).hexdigest()
        if not secrets.compare_digest(candidate_hash, key.hashed_key):
            raise AuthenticationFailed("Invalid API key")

        # Cross-tenant guard: when the request's Host already resolved to a
        # tenant, it MUST match the key's tenant. Defends against using a
        # tenant-A key against tenant-B's subdomain.
        request_tenant = getattr(request, "tenant", None)
        if request_tenant and request_tenant.pk != key.tenant_id:
            raise AuthenticationFailed("API key not valid for this tenant")

        # Best-effort usage log (single UPDATE; no signals; never blocks auth).
        APIKey.unscoped.filter(pk=key.pk).update(
            last_used_at=timezone.now(),
            last_used_ip=request.META.get("REMOTE_ADDR") or None,
            last_used_user_agent=request.META.get("HTTP_USER_AGENT", "")[:255],
            request_count=F("request_count") + 1,
        )

        return (key.service_user, key)

    def authenticate_header(self, request):
        return self.keyword
