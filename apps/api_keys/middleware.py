"""
Response middleware that emits per-key ``X-RateLimit-*`` headers when an API
key was used. Pure read-side — it only inspects ``request._crm_throttle_info``
set by :class:`apps.api_keys.throttling.APIKeyRateThrottle`.
"""

from __future__ import annotations


class RateLimitHeadersMiddleware:
    """
    Django-style middleware. Emits ``X-RateLimit-Limit``, ``X-RateLimit-Remaining``,
    and ``X-RateLimit-Reset`` (epoch seconds) on outbound responses when the
    APIKeyRateThrottle stashed bucket info on the request.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        info = getattr(request, "_crm_throttle_info", None)
        if info is not None:
            try:
                limit, remaining, reset_epoch = info
            except (TypeError, ValueError):
                return response
            response["X-RateLimit-Limit"] = str(limit)
            response["X-RateLimit-Remaining"] = str(remaining)
            response["X-RateLimit-Reset"] = str(reset_epoch)
        return response
