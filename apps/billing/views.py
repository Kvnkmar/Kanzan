"""
DRF ViewSets and views for the billing app.

* ``PlanViewSet``          -- public, read-only plan listing
* ``SubscriptionViewSet``  -- tenant-scoped subscription management
* ``InvoiceViewSet``       -- tenant-scoped invoice history
* ``UsageViewSet``         -- tenant-scoped usage counters
* ``create_checkout_session`` -- creates a Stripe Checkout session for upgrading
"""

import logging

import stripe
from django.conf import settings
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response

from apps.accounts.permissions import IsTenantAdmin

from apps.billing.models import Invoice, Plan, Subscription, UsageTracker
from apps.billing.serializers import (
    InvoiceSerializer,
    PlanSerializer,
    SubscriptionSerializer,
    UsageSerializer,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ViewSets
# ---------------------------------------------------------------------------


class PlanViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Public, read-only listing of available billing plans.

    No authentication required -- this powers the pricing page.
    """

    queryset = Plan.objects.filter(is_active=True)
    serializer_class = PlanSerializer
    permission_classes = [permissions.AllowAny]
    pagination_class = None


class SubscriptionViewSet(viewsets.GenericViewSet):
    """
    Tenant-scoped subscription management.

    - **retrieve**: view the current subscription
    - **cancel**: mark the subscription for cancellation at period end
    - **reactivate**: remove the cancellation flag
    """

    serializer_class = SubscriptionSerializer
    permission_classes = [permissions.IsAuthenticated, IsTenantAdmin]

    def _get_tenant(self, request):
        tenant = getattr(request, "tenant", None)
        if tenant is None:
            raise NotFound("No tenant context available.")
        return tenant

    def _get_subscription(self, request):
        tenant = self._get_tenant(request)
        try:
            return Subscription.objects.select_related("plan").get(tenant=tenant)
        except Subscription.DoesNotExist:
            raise NotFound("No subscription found for this tenant.")

    def retrieve(self, request, *args, **kwargs):
        """Return the current tenant's subscription."""
        subscription = self._get_subscription(request)
        serializer = self.get_serializer(subscription)
        return Response(serializer.data)

    @action(detail=False, methods=["post"], url_path="cancel")
    def cancel(self, request):
        """
        Cancel the subscription at the end of the current billing period.

        This sets ``cancel_at_period_end=True`` both locally and on Stripe.
        """
        subscription = self._get_subscription(request)

        if not subscription.is_active:
            raise ValidationError("Subscription is not active.")

        # Tell Stripe to cancel at period end.
        if subscription.stripe_subscription_id:
            try:
                stripe.api_key = settings.STRIPE_SECRET_KEY
                stripe.Subscription.modify(
                    subscription.stripe_subscription_id,
                    cancel_at_period_end=True,
                )
            except stripe.error.StripeError as exc:
                logger.error("Stripe cancellation error: %s", exc)
                raise ValidationError("Failed to cancel subscription with Stripe.")

        subscription.cancel_at_period_end = True
        subscription.save(update_fields=["cancel_at_period_end", "updated_at"])

        serializer = self.get_serializer(subscription)
        return Response(serializer.data)

    @action(detail=False, methods=["post"], url_path="reactivate")
    def reactivate(self, request):
        """
        Remove the pending cancellation on the subscription.
        """
        subscription = self._get_subscription(request)

        if not subscription.cancel_at_period_end:
            raise ValidationError("Subscription is not pending cancellation.")

        if subscription.stripe_subscription_id:
            try:
                stripe.api_key = settings.STRIPE_SECRET_KEY
                stripe.Subscription.modify(
                    subscription.stripe_subscription_id,
                    cancel_at_period_end=False,
                )
            except stripe.error.StripeError as exc:
                logger.error("Stripe reactivation error: %s", exc)
                raise ValidationError("Failed to reactivate subscription with Stripe.")

        subscription.cancel_at_period_end = False
        subscription.save(update_fields=["cancel_at_period_end", "updated_at"])

        serializer = self.get_serializer(subscription)
        return Response(serializer.data)


class InvoiceViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Tenant-scoped, read-only invoice history.
    """

    serializer_class = InvoiceSerializer
    permission_classes = [permissions.IsAuthenticated, IsTenantAdmin]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if tenant is None:
            return Invoice.objects.none()
        return Invoice.objects.filter(
            subscription__tenant=tenant,
        ).select_related("subscription")


class UsageViewSet(viewsets.GenericViewSet):
    """
    Tenant-scoped usage statistics.
    """

    serializer_class = UsageSerializer
    permission_classes = [permissions.IsAuthenticated]

    def retrieve(self, request, *args, **kwargs):
        """Return the current tenant's usage counters."""
        tenant = getattr(request, "tenant", None)
        if tenant is None:
            raise NotFound("No tenant context available.")
        try:
            usage = UsageTracker.objects.get(tenant=tenant)
        except UsageTracker.DoesNotExist:
            raise NotFound("No usage data found for this tenant.")
        serializer = self.get_serializer(usage)
        return Response(serializer.data)


# ---------------------------------------------------------------------------
# Stripe Checkout
# ---------------------------------------------------------------------------


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated, IsTenantAdmin])
def create_checkout_session(request):
    """
    Create a Stripe Checkout Session for upgrading/changing plans.

    Expects JSON body::

        {
            "plan_id": "<uuid>",
            "billing_cycle": "monthly" | "yearly",
            "success_url": "https://...",
            "cancel_url": "https://..."
        }
    """
    tenant = getattr(request, "tenant", None)
    if tenant is None:
        raise NotFound("No tenant context available.")

    plan_id = request.data.get("plan_id")
    billing_cycle = request.data.get("billing_cycle", "monthly")
    success_url = request.data.get("success_url")
    cancel_url = request.data.get("cancel_url")

    if not plan_id:
        raise ValidationError({"plan_id": "This field is required."})
    if not success_url:
        raise ValidationError({"success_url": "This field is required."})
    if not cancel_url:
        raise ValidationError({"cancel_url": "This field is required."})

    try:
        plan = Plan.objects.get(id=plan_id, is_active=True)
    except Plan.DoesNotExist:
        raise NotFound("Plan not found.")

    # Determine the correct Stripe price ID.
    if billing_cycle == "yearly":
        stripe_price_id = plan.stripe_price_yearly
    else:
        stripe_price_id = plan.stripe_price_monthly

    if not stripe_price_id:
        raise ValidationError(
            f"No Stripe price configured for the {billing_cycle} billing cycle "
            f"on the {plan.name} plan."
        )

    # Resolve or create the Stripe customer ID.
    stripe.api_key = settings.STRIPE_SECRET_KEY

    customer_id = None
    try:
        subscription = tenant.subscription
        customer_id = subscription.stripe_customer_id
    except Subscription.DoesNotExist:
        pass

    checkout_params = {
        "mode": "subscription",
        "line_items": [{"price": stripe_price_id, "quantity": 1}],
        "success_url": success_url,
        "cancel_url": cancel_url,
        "client_reference_id": str(tenant.id),
        "metadata": {
            "tenant_id": str(tenant.id),
            "plan_tier": plan.tier,
        },
        "subscription_data": {
            "metadata": {
                "tenant_id": str(tenant.id),
            },
        },
    }

    if customer_id:
        checkout_params["customer"] = customer_id
    else:
        checkout_params["customer_email"] = request.user.email

    try:
        session = stripe.checkout.Session.create(**checkout_params)
    except stripe.error.StripeError as exc:
        logger.error("Stripe Checkout error: %s", exc)
        raise ValidationError("Failed to create Stripe Checkout session.")

    return Response(
        {
            "checkout_url": session.url,
            "session_id": session.id,
        },
        status=status.HTTP_201_CREATED,
    )
