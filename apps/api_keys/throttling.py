"""
Per-key rate throttle.

Extends DRF's ``SimpleRateThrottle`` so each ``APIKey`` row gets its own
bucket (keyed on the APIKey PK rather than the user PK — important because
revoked/regenerated keys mustn't carry forward old usage counts). When a
bucket is engaged, stashes ``(limit, remaining, reset_epoch)`` on the
request so :mod:`apps.api_keys.middleware` can emit ``X-RateLimit-*``
headers on the way out.

We use ``SimpleRateThrottle`` (not ``ScopedRateThrottle``) so the throttle
engages on every API-key-authenticated request without requiring each
viewset to opt-in via a ``throttle_scope`` class attribute. Non-API-key
traffic (JWT/session) is skipped by returning ``None`` from
``get_cache_key``.
"""

from __future__ import annotations

from rest_framework.throttling import SimpleRateThrottle

from apps.api_keys.models import APIKey


class APIKeyRateThrottle(SimpleRateThrottle):
    """
    Per-APIKey bucket. Engages only when ``request.auth`` is an APIKey;
    other auth schemes (JWT/session/anonymous) pass through unaffected.
    Rate is read from ``DEFAULT_THROTTLE_RATES["api_key"]`` (settings).
    """

    scope = "api_key"

    def get_cache_key(self, request, view):
        auth = getattr(request, "auth", None)
        if not isinstance(auth, APIKey):
            return None  # not an API-key request — skip throttling entirely
        return self.cache_format % {
            "scope": self.scope,
            "ident": f"apikey-{auth.pk}",
        }

    def allow_request(self, request, view):
        allowed = super().allow_request(request, view)

        # Stash header info whenever this throttle engaged a bucket
        # (i.e. ``get_cache_key`` returned a non-None value).
        # IMPORTANT: ``request`` here is a DRF ``Request`` wrapper. The
        # Django middleware on the response path reads from the underlying
        # ``HttpRequest`` (``request._request``) — we set the attribute on
        # *both* so the middleware finds it regardless of which one it
        # holds a reference to.
        auth = getattr(request, "auth", None)
        if isinstance(auth, APIKey) and getattr(self, "history", None) is not None and getattr(self, "now", None) is not None:
            num_requests = getattr(self, "num_requests", 0) or 0
            remaining = max(0, num_requests - len(self.history))
            duration = getattr(self, "duration", 0) or 0
            reset_epoch = int(self.now + duration)
            info = (num_requests, remaining, reset_epoch)
            request._kanzan_throttle_info = info
            underlying = getattr(request, "_request", None)
            if underlying is not None:
                underlying._kanzan_throttle_info = info

        return allowed
