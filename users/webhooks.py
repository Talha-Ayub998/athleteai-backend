# users/webhooks.py
from __future__ import annotations

import os
import logging
from datetime import datetime

import stripe
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from users.models import CustomUser, Subscription, ReportPurchase

# --- Stripe keys from environment
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

logger = logging.getLogger(__name__)


def _find_user_for_session(session_obj: dict) -> CustomUser | None:
    """Resolve app user for a Checkout Session via metadata.user_id or email."""
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


def _plan_interval_from_subscription(stripe_sub: dict, session_meta: dict | None = None):
    """
    Derive (plan, interval) primarily from the subscription item's price.lookup_key.
    Fallback to session metadata (plan/interval) if lookup_key is absent.
    """
    items = (stripe_sub.get("items") or {}).get("data") or []
    price = items[0].get("price") if items else {}
    lookup_key = price.get("lookup_key")

    plan = interval = None
    if lookup_key and "_" in lookup_key:
        try:
            plan, interval = lookup_key.split("_", 1)  # e.g., "essentials_month"
        except ValueError:
            plan = interval = None

    # Fallback to session metadata if needed (only available in checkout.session.completed)
    if (not plan or not interval) and session_meta:
        plan = plan or session_meta.get("plan")
        interval = interval or session_meta.get("interval")

    # Optional last-ditch fallback from price object (amount + interval)
    if (not plan or not interval) and price:
        unit = price.get("unit_amount")
        rec = price.get("recurring") or {}
        interval = interval or rec.get("interval")  # "month" or "year"
        if unit in (399, 3840):
            plan = plan or "essentials"
        elif unit in (799, 7670):
            plan = plan or "precision"

    return plan, interval


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
    endpoint_secret = STRIPE_WEBHOOK_SECRET

    if not endpoint_secret:
        logger.error("STRIPE_WEBHOOK_SECRET missing; refusing webhook")
        return HttpResponse(status=400)

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
    except ValueError:
        logger.warning("Stripe webhook: invalid payload")
        return HttpResponse(status=400)
    except stripe.error.SignatureVerificationError:
        logger.warning("Stripe webhook: signature verification failed")
        return HttpResponse(status=400)

    etype = event.get("type")
    data = event.get("data", {}).get("object", {})
    logger.info("Stripe webhook received: %s", etype)

    # 1) Checkout completed (subscription or one-time)
    if etype == "checkout.session.completed":
        session = data
        mode = session.get("mode")
        user = _find_user_for_session(session)

        if user is None:
            logger.warning("checkout.session.completed: unable to match user")
            return HttpResponse(status=200)

        customer_id = session.get("customer")
        session_meta = session.get("metadata") or {}

        with transaction.atomic():
            sub_rec, _ = Subscription.objects.get_or_create(user=user)
            if customer_id and sub_rec.stripe_customer_id != customer_id:
                sub_rec.stripe_customer_id = customer_id
                sub_rec.save(update_fields=["stripe_customer_id"])

            if mode == "subscription":
                sub_id = session.get("subscription")
                if not sub_id:
                    logger.error("checkout.session.completed missing subscription id")
                    return HttpResponse(status=200)

                # Expand price so we get lookup_key
                stripe_sub = stripe.Subscription.retrieve(sub_id, expand=["items.data.price"])
                plan, interval = _plan_interval_from_subscription(stripe_sub, session_meta)

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

        return HttpResponse(status=200)

    # 2) Subscription lifecycle (created/updated/deleted)
    if etype in (
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    ):
        stripe_sub = data
        customer_id = stripe_sub.get("customer")
        sub_id = stripe_sub.get("id")

        sub_rec = Subscription.objects.filter(stripe_subscription_id=sub_id).first()
        if sub_rec is None and customer_id:
            sub_rec = Subscription.objects.filter(stripe_customer_id=customer_id).first()

        # If still missing, try to resolve the user from the Stripe customer to create it
        if sub_rec is None and customer_id:
            try:
                sc = stripe.Customer.retrieve(customer_id)
                email = sc.get("email")
                user = CustomUser.objects.filter(email=email).first() if email else None
                if user:
                    sub_rec, _ = Subscription.objects.get_or_create(
                        user=user, defaults={"stripe_customer_id": customer_id}
                    )
            except Exception as e:
                logger.exception("Could not resolve user from customer %s: %s", customer_id, e)

        if sub_rec is None:
            logger.info("Subscription event for unknown local record; ignoring.")
            return HttpResponse(status=200)

        # Plan/interval from lookup_key
        plan, interval = _plan_interval_from_subscription(stripe_sub)

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
            # Immediate downgrade to free
            sub_rec.plan = "free"
            sub_rec.interval = None
            sub_rec.stripe_subscription_id = None
            sub_rec.status = "canceled"

        sub_rec.save()
        return HttpResponse(status=200)

    # 3) Invoice succeeded → refresh status/period end (renewals)
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

    # 4) Invoice failed → mark past_due
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
