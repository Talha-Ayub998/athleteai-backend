# users/webhooks.py
from __future__ import annotations

import os
import json
import logging
from datetime import datetime

import stripe
from django.conf import settings
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from users.models import CustomUser, Subscription, ReportPurchase
from .stripe_prices import STRIPE_PRICES

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
logger = logging.getLogger(__name__)

# Build reverse lookup once (price_id -> key)
REVERSE_PRICE = {v: k for k, v in STRIPE_PRICES.items()}


def _plan_interval_from_price(price_id: str):
    """
    Map a subscription price_id to (plan, interval).
    Returns (None, None) if the price_id isn't a subscription price (e.g., one-time).
    """
    key = REVERSE_PRICE.get(price_id)
    if not key or key == "pdf_report":
        return (None, None)
    try:
        plan, interval = key.split("_", 1)  # e.g. "essentials_month" -> ("essentials", "month")
        return (plan, interval)
    except ValueError:
        return (None, None)


def _find_user_for_session(session_obj: dict) -> CustomUser | None:
    """
    Resolve the app user for a Checkout Session.
    Prefers metadata.user_id (set when creating the session), falls back to email.
    """
    meta = session_obj.get("metadata") or {}
    user_id = meta.get("user_id")
    if user_id:
        try:
            return CustomUser.objects.get(id=user_id)
        except CustomUser.DoesNotExist:
            pass

    email = (
        (session_obj.get("customer_details") or {}).get("email")
        or session_obj.get("customer_email")
    )
    if email:
        try:
            return CustomUser.objects.get(email=email)
        except CustomUser.DoesNotExist:
            return None
    return None


@csrf_exempt
def stripe_webhook(request):
    """
    Stripe webhook endpoint. Verifies signatures and updates local state:
      - checkout.session.completed (subscription vs one-time)
      - customer.subscription.created/updated/deleted
      - invoice.payment_succeeded / invoice.payment_failed
    """
    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE")
    endpoint_secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", None)

    if not endpoint_secret:
        logger.error("STRIPE_WEBHOOK_SECRET is not configured")
        return HttpResponse(status=500)

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
    except ValueError:
        # Invalid payload
        return HttpResponse(status=400)
    except stripe.error.SignatureVerificationError:
        # Invalid signature
        return HttpResponse(status=400)

    etype = event.get("type")
    data = event.get("data", {}).get("object", {})

    # 1) Checkout completed (covers both subscription and one-time)
    if etype == "checkout.session.completed":
        session = data
        mode = session.get("mode")
        user = _find_user_for_session(session)

        if user is None:
            logger.warning("checkout.session.completed: unable to match user")
            return HttpResponse(status=200)  # ack so Stripe stops retries

        customer_id = session.get("customer")

        with transaction.atomic():
            sub_rec, _ = Subscription.objects.get_or_create(user=user)
            # Store/refresh Stripe customer id
            if customer_id and sub_rec.stripe_customer_id != customer_id:
                sub_rec.stripe_customer_id = customer_id
                sub_rec.save(update_fields=["stripe_customer_id"])

            if mode == "subscription":
                sub_id = session.get("subscription")
                if not sub_id:
                    logger.error("checkout.session.completed missing subscription id")
                    return HttpResponse(status=200)

                stripe_sub = stripe.Subscription.retrieve(sub_id, expand=["items.data.price"])
                items = stripe_sub.get("items", {}).get("data", [])
                price_id = items[0]["price"]["id"] if items else None
                plan, interval = _plan_interval_from_price(price_id) if price_id else (None, None)

                sub_rec.plan = plan or sub_rec.plan or "free"
                sub_rec.interval = interval
                sub_rec.stripe_subscription_id = sub_id
                sub_rec.status = stripe_sub.get("status", sub_rec.status)

                cpe_unix = stripe_sub.get("current_period_end")
                sub_rec.current_period_end = (
                    datetime.fromtimestamp(cpe_unix, tz=timezone.utc) if cpe_unix else None
                )
                sub_rec.cancel_at_period_end = bool(stripe_sub.get("cancel_at_period_end"))
                sub_rec.save()

            elif mode == "payment":
                # One-time purchase (e.g., PDF report)
                pi = session.get("payment_intent")
                amount_total = session.get("amount_total")  # cents
                if pi and not ReportPurchase.objects.filter(stripe_payment_intent=pi).exists():
                    ReportPurchase.objects.create(
                        user=user,
                        stripe_payment_intent=pi,
                        amount=amount_total or 0,
                    )
                # No subscription field changes for one-time payments.

        return HttpResponse(status=200)

    # 2) Keep subscription in sync with lifecycle events
    if etype in (
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    ):
        stripe_sub = data
        customer_id = stripe_sub.get("customer")
        sub_id = stripe_sub.get("id")

        # Prefer matching by stored subscription id
        sub_rec = Subscription.objects.filter(stripe_subscription_id=sub_id).first()

        # Fallback by customer id (helps first-time events)
        if sub_rec is None and customer_id:
            sub_rec = Subscription.objects.filter(stripe_customer_id=customer_id).first()

        if sub_rec is None:
            logger.info("Subscription event for unknown local record; ignoring.")
            return HttpResponse(status=200)

        items = stripe_sub.get("items", {}).get("data", [])
        price_id = items[0]["price"]["id"] if items else None
        plan, interval = _plan_interval_from_price(price_id) if price_id else (None, None)

        if plan:
            sub_rec.plan = plan
        if interval:
            sub_rec.interval = interval

        sub_rec.status = stripe_sub.get("status", sub_rec.status)

        cpe_unix = stripe_sub.get("current_period_end")
        sub_rec.current_period_end = (
            datetime.fromtimestamp(cpe_unix, tz=timezone.utc) if cpe_unix else None
        )
        sub_rec.cancel_at_period_end = bool(stripe_sub.get("cancel_at_period_end"))

        if etype == "customer.subscription.deleted":
            # Immediate downgrade to free on deletion
            sub_rec.plan = "free"
            sub_rec.interval = None
            sub_rec.stripe_subscription_id = None
            sub_rec.status = "canceled"

        sub_rec.save()
        return HttpResponse(status=200)

    # 3) Invoice succeeded -> refresh status/period end (covers renewals)
    if etype == "invoice.payment_succeeded":
        invoice = data
        sub_id = invoice.get("subscription")
        if sub_id:
            sub_rec = Subscription.objects.filter(stripe_subscription_id=sub_id).first()
            if sub_rec:
                try:
                    sub = stripe.Subscription.retrieve(sub_id, expand=["items.data.price"])
                    sub_rec.status = sub.get("status", sub_rec.status)
                    cpe = sub.get("current_period_end")
                    sub_rec.current_period_end = (
                        datetime.fromtimestamp(cpe, tz=timezone.utc) if cpe else None
                    )
                    sub_rec.cancel_at_period_end = bool(sub.get("cancel_at_period_end"))
                    sub_rec.save()
                except Exception as e:
                    logger.exception("Failed to refresh subscription after invoice.payment_succeeded: %s", e)
        return HttpResponse(status=200)

    # 4) Invoice failed -> mark past_due (UI can prompt user to update card)
    if etype == "invoice.payment_failed":
        invoice = data
        sub_id = invoice.get("subscription")
        sub_rec = Subscription.objects.filter(stripe_subscription_id=sub_id).first()
        if sub_rec:
            sub_rec.status = "past_due"
            sub_rec.save(update_fields=["status"])
        return HttpResponse(status=200)

    # Acknowledge all other events
    return HttpResponse(status=200)


def payment_success(request):
    session_id = request.GET.get("session_id")
    return render(request, "payment_success.html", {"session_id": session_id})


def payment_cancel(request):
    return render(request, "payment_cancel.html")
