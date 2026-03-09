"""
Stripe webhook handler.

Receives events from Stripe and synchronises local billing state
(Subscription, Invoice) accordingly.

Wire this view into ``urls.py`` at ``/api/v1/billing/webhook/``.
"""

import logging
from datetime import datetime, timezone as dt_tz

import stripe
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.billing.models import Invoice, Plan, Subscription

logger = logging.getLogger(__name__)


def _ts_to_dt(ts):
    """Convert a Unix timestamp (int) to a timezone-aware datetime, or None."""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=dt_tz.utc)


def _sync_subscription_from_stripe(stripe_sub):
    """
    Create or update a local ``Subscription`` record from Stripe subscription
    data.
    """
    stripe_sub_id = stripe_sub["id"]
    stripe_customer_id = stripe_sub["customer"]

    # Determine billing cycle from the price interval.
    interval = "monthly"
    items_data = stripe_sub.get("items", {}).get("data", [])
    if items_data:
        price = items_data[0].get("price", {})
        recurring = price.get("recurring", {})
        if recurring.get("interval") == "year":
            interval = "yearly"

    # Map the Stripe price ID to a local Plan.
    stripe_price_id = ""
    if items_data:
        stripe_price_id = items_data[0].get("price", {}).get("id", "")

    plan = None
    if stripe_price_id:
        plan = Plan.objects.filter(
            stripe_price_monthly=stripe_price_id,
        ).first() or Plan.objects.filter(
            stripe_price_yearly=stripe_price_id,
        ).first()

    # Fallback: try to match by product ID.
    if plan is None:
        product_id = ""
        if items_data:
            product_id = items_data[0].get("price", {}).get("product", "")
        if product_id:
            plan = Plan.objects.filter(stripe_product_id=product_id).first()

    if plan is None:
        logger.error(
            "Could not resolve Plan for Stripe subscription %s (price=%s)",
            stripe_sub_id,
            stripe_price_id,
        )
        return None

    # Map Stripe status to our local status choices.
    status_map = {
        "trialing": Subscription.Status.TRIALING,
        "active": Subscription.Status.ACTIVE,
        "past_due": Subscription.Status.PAST_DUE,
        "canceled": Subscription.Status.CANCELED,
        "incomplete": Subscription.Status.INCOMPLETE,
        "unpaid": Subscription.Status.UNPAID,
        "incomplete_expired": Subscription.Status.CANCELED,
        "paused": Subscription.Status.CANCELED,
    }
    local_status = status_map.get(stripe_sub["status"], Subscription.Status.INCOMPLETE)

    defaults = {
        "plan": plan,
        "stripe_customer_id": stripe_customer_id,
        "status": local_status,
        "billing_cycle": interval,
        "current_period_start": _ts_to_dt(stripe_sub.get("current_period_start")),
        "current_period_end": _ts_to_dt(stripe_sub.get("current_period_end")),
        "cancel_at_period_end": stripe_sub.get("cancel_at_period_end", False),
        "canceled_at": _ts_to_dt(stripe_sub.get("canceled_at")),
        "trial_end": _ts_to_dt(stripe_sub.get("trial_end")),
    }

    subscription, created = Subscription.objects.update_or_create(
        stripe_subscription_id=stripe_sub_id,
        defaults=defaults,
    )

    action = "Created" if created else "Updated"
    logger.info(
        "%s subscription %s for customer %s (status=%s, plan=%s)",
        action,
        stripe_sub_id,
        stripe_customer_id,
        local_status,
        plan.tier,
    )
    return subscription


def _handle_subscription_created(event):
    """Handle ``customer.subscription.created``."""
    stripe_sub = event["data"]["object"]
    _sync_subscription_from_stripe(stripe_sub)


def _handle_subscription_updated(event):
    """Handle ``customer.subscription.updated``."""
    stripe_sub = event["data"]["object"]
    _sync_subscription_from_stripe(stripe_sub)


def _handle_subscription_deleted(event):
    """Handle ``customer.subscription.deleted``."""
    stripe_sub = event["data"]["object"]
    stripe_sub_id = stripe_sub["id"]

    try:
        subscription = Subscription.objects.get(stripe_subscription_id=stripe_sub_id)
        subscription.status = Subscription.Status.CANCELED
        subscription.canceled_at = _ts_to_dt(stripe_sub.get("canceled_at"))
        subscription.save(update_fields=["status", "canceled_at", "updated_at"])
        logger.info("Canceled subscription %s", stripe_sub_id)
    except Subscription.DoesNotExist:
        logger.warning(
            "Received subscription.deleted for unknown subscription %s",
            stripe_sub_id,
        )


def _handle_invoice_paid(event):
    """Handle ``invoice.paid``."""
    stripe_invoice = event["data"]["object"]
    _sync_invoice(stripe_invoice)


def _handle_invoice_payment_failed(event):
    """Handle ``invoice.payment_failed``."""
    stripe_invoice = event["data"]["object"]
    _sync_invoice(stripe_invoice)


def _sync_invoice(stripe_invoice):
    """Create or update a local ``Invoice`` record from Stripe invoice data."""
    stripe_invoice_id = stripe_invoice["id"]
    stripe_sub_id = stripe_invoice.get("subscription")

    if not stripe_sub_id:
        logger.info("Invoice %s has no subscription; skipping.", stripe_invoice_id)
        return

    try:
        subscription = Subscription.objects.get(stripe_subscription_id=stripe_sub_id)
    except Subscription.DoesNotExist:
        logger.warning(
            "Invoice %s references unknown subscription %s; skipping.",
            stripe_invoice_id,
            stripe_sub_id,
        )
        return

    # Stripe amounts are in cents; convert to dollars.
    amount_paid = stripe_invoice.get("amount_paid", 0)
    amount = amount_paid / 100

    defaults = {
        "subscription": subscription,
        "amount": amount,
        "currency": stripe_invoice.get("currency", "usd"),
        "status": stripe_invoice.get("status", "unknown"),
        "invoice_pdf_url": stripe_invoice.get("invoice_pdf", "") or "",
        "hosted_invoice_url": stripe_invoice.get("hosted_invoice_url", "") or "",
        "period_start": _ts_to_dt(stripe_invoice.get("period_start")),
        "period_end": _ts_to_dt(stripe_invoice.get("period_end")),
    }

    invoice, created = Invoice.objects.update_or_create(
        stripe_invoice_id=stripe_invoice_id,
        defaults=defaults,
    )
    action = "Created" if created else "Updated"
    logger.info("%s invoice %s (status=%s)", action, stripe_invoice_id, defaults["status"])


# ---------------------------------------------------------------------------
# Event dispatch table
# ---------------------------------------------------------------------------

EVENT_HANDLERS = {
    "customer.subscription.created": _handle_subscription_created,
    "customer.subscription.updated": _handle_subscription_updated,
    "customer.subscription.deleted": _handle_subscription_deleted,
    "invoice.paid": _handle_invoice_paid,
    "invoice.payment_failed": _handle_invoice_payment_failed,
}


# ---------------------------------------------------------------------------
# Webhook endpoint
# ---------------------------------------------------------------------------


@csrf_exempt
@require_POST
def stripe_webhook(request):
    """
    Stripe webhook endpoint.

    Verifies the event signature, dispatches to the appropriate handler,
    and returns a 200 OK to acknowledge receipt.
    """
    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")
    webhook_secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", "")

    if not webhook_secret:
        logger.error("STRIPE_WEBHOOK_SECRET is not configured.")
        return JsonResponse({"detail": "Webhook not configured."}, status=500)

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except ValueError:
        logger.warning("Invalid webhook payload.")
        return JsonResponse({"detail": "Invalid payload."}, status=400)
    except stripe.error.SignatureVerificationError:
        logger.warning("Invalid webhook signature.")
        return JsonResponse({"detail": "Invalid signature."}, status=400)

    event_type = event.get("type", "")
    logger.info("Received Stripe event: %s (id=%s)", event_type, event.get("id"))

    handler = EVENT_HANDLERS.get(event_type)
    if handler is not None:
        try:
            handler(event)
        except Exception:
            logger.exception("Error handling Stripe event %s", event_type)
            return JsonResponse({"detail": "Webhook handler error."}, status=500)
    else:
        logger.debug("Unhandled Stripe event type: %s", event_type)

    return HttpResponse(status=200)
