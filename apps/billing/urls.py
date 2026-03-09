"""
URL configuration for the billing app.

Include in the project root URLconf::

    path("api/v1/billing/", include("apps.billing.urls")),
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.billing.views import (
    InvoiceViewSet,
    PlanViewSet,
    SubscriptionViewSet,
    UsageViewSet,
    create_checkout_session,
)
from apps.billing.webhooks import stripe_webhook

router = DefaultRouter()
router.register(r"plans", PlanViewSet, basename="plan")
router.register(r"invoices", InvoiceViewSet, basename="invoice")

app_name = "billing"

urlpatterns = [
    # Stripe webhook -- must be outside the router and CSRF-exempt.
    path("webhook/", stripe_webhook, name="stripe-webhook"),
    # Subscription singleton (not router-registered because it's not a
    # standard CRUD resource -- it's always the current tenant's subscription).
    path(
        "subscription/",
        SubscriptionViewSet.as_view({"get": "retrieve"}),
        name="subscription-detail",
    ),
    path(
        "subscription/cancel/",
        SubscriptionViewSet.as_view({"post": "cancel"}),
        name="subscription-cancel",
    ),
    path(
        "subscription/reactivate/",
        SubscriptionViewSet.as_view({"post": "reactivate"}),
        name="subscription-reactivate",
    ),
    # Usage singleton.
    path(
        "usage/",
        UsageViewSet.as_view({"get": "retrieve"}),
        name="usage-detail",
    ),
    # Stripe Checkout session creation.
    path("checkout/", create_checkout_session, name="create-checkout-session"),
    # Router-generated CRUD URLs (plans, invoices).
    path("", include(router.urls)),
]
