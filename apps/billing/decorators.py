"""
Billing decorators for feature-flag enforcement.

Usage::

    @require_feature("api_access")
    def my_api_view(request):
        ...

    @require_feature("sso")
    def sso_login(request):
        ...
"""

import functools
import logging

from django.http import JsonResponse

logger = logging.getLogger(__name__)


def require_feature(feature_name):
    """
    View decorator that checks whether the current tenant's plan has the
    given feature enabled.

    The feature name is mapped to the ``Plan`` model's boolean flag field
    ``has_<feature_name>``.  For example, ``require_feature("api_access")``
    checks ``plan.has_api_access``.

    Returns HTTP 403 if the feature is not available on the tenant's plan.
    """

    def decorator(view_func):
        @functools.wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            tenant = getattr(request, "tenant", None)

            if tenant is None:
                # No tenant context -- cannot check features, deny by default.
                return JsonResponse(
                    {
                        "detail": "Tenant context required.",
                        "code": "no_tenant",
                    },
                    status=403,
                )

            # Resolve the plan via the subscription.
            try:
                plan = tenant.subscription.plan
            except tenant.__class__.subscription.RelatedObjectDoesNotExist:
                # No subscription -- fall back to free plan.
                from apps.billing.models import Plan

                plan = Plan.objects.filter(tier=Plan.Tier.FREE).first()

            if plan is None:
                return JsonResponse(
                    {
                        "detail": "No billing plan configured.",
                        "code": "no_plan",
                    },
                    status=403,
                )

            flag_attr = f"has_{feature_name}"
            has_feature = getattr(plan, flag_attr, False)

            if not has_feature:
                logger.info(
                    "Feature '%s' denied for tenant %s (plan=%s)",
                    feature_name,
                    tenant.slug,
                    plan.tier,
                )
                return JsonResponse(
                    {
                        "detail": (
                            f"The '{feature_name}' feature is not available on your "
                            f"{plan.name} plan. Please upgrade to access this feature."
                        ),
                        "code": "feature_not_available",
                        "feature": feature_name,
                        "current_plan": plan.tier,
                    },
                    status=403,
                )

            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator
