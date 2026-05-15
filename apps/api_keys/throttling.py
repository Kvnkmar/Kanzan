"""
Per-key rate throttle.

Extends DRF's ``ScopedRateThrottle`` so each ``APIKey`` row gets its own
bucket (keyed on the APIKey PK rather than the user PK — important because
revoked/regenerated keys mustn't carry forward old usage counts). When a
bucket is engaged, stashes ``(limit, remaining, reset_epoch)`` on the
request so :mod:`apps.api_keys.middleware` can emit ``X-RateLimit-*``
headers on the way out.
"""

from __future__ import annotations

from rest_framework.throttling import ScopedRateThrottle

from apps.api_keys.models import APIKey


class APIKeyRateThrottle(ScopedRateThrottle):
    """
    Per-APIKey bucket. Falls through to the default identity (IP/user) when
    the request is NOT authenticated via an API key, so JWT/session traffic
    is unaffected.
    """

    scope = "api_key"

    def get_ident(self, request):
        auth = getattr(request, "auth", None)
        if isinstance(auth, APIKey):
            return f"apikey-{auth.pk}"
        return super().get_ident(request)

    def allow_request(self, request, view):
        allowed = super().allow_request(request, view)

        # Only stash header info when this throttle actually engaged a bucket
        # (i.e. it has a self.history populated for an APIKey ident).
        auth = getattr(request, "auth", None)
        if isinstance(auth, APIKey) and getattr(self, "history", None) is not None and getattr(self, "now", None) is not None:
            num_requests = getattr(self, "num_requests", 0) or 0
            remaining = max(0, num_requests - len(self.history))
            duration = getattr(self, "duration", 0) or 0
            reset_epoch = int(self.now + duration)
            request._kanzan_throttle_info = (num_requests, remaining, reset_epoch)

        return allowed
