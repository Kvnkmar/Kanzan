"""
Subscription enforcement middleware.

Checks that the current tenant has an active (or grace-period) subscription
before allowing the request to proceed.  Returns HTTP 402 Payment Required
when the subscription is lapsed.
"""

import json
import logging

from django.http import JsonResponse

logger = logging.getLogger(__name__)

# Paths that bypass subscription enforcement entirely.
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
    "/login/",
    "/register/",
    "/logout/",
    "/billing/",
    "/favicon",
)


class SubscriptionMiddleware:
    """
    Deny access with 402 when the tenant's subscription is neither active
    nor within the grace period.

    Must be placed AFTER ``TenantMiddleware`` in the ``MIDDLEWARE`` list so
    that ``request.tenant`` is already resolved.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    @staticmethod
    def _is_exempt(path: str) -> bool:
        """Return True if *path* should bypass subscription checks."""
        return any(path.startswith(prefix) for prefix in EXEMPT_PATH_PREFIXES)

    def __call__(self, request):
        path = request.path

        if self._is_exempt(path):
            return self.get_response(request)

        tenant = getattr(request, "tenant", None)

        # No tenant context (main site, public endpoints) -- allow through.
        if tenant is None:
            return self.get_response(request)

        # If the tenant has no subscription at all, treat as free tier -- allow.
        subscription = getattr(tenant, "subscription", None)
        if subscription is None:
            try:
                # OneToOneField raises RelatedObjectDoesNotExist when absent.
                subscription = tenant.subscription
            except tenant.__class__.subscription.RelatedObjectDoesNotExist:
                return self.get_response(request)

        # Active or trialing subscriptions are fine.
        if subscription.is_active:
            return self.get_response(request)

        # Past-due but still within the 7-day grace window.
        if subscription.in_grace_period:
            return self.get_response(request)

        # Subscription is lapsed -- block access.
        logger.warning(
            "Tenant %s blocked: subscription status=%s",
            tenant.slug,
            subscription.status,
        )
        return JsonResponse(
            {
                "detail": "Your subscription is inactive. Please update your billing information to continue.",
                "code": "subscription_inactive",
                "status": subscription.status,
            },
            status=402,
        )
