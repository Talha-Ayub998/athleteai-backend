# users/webhooks.py
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponse
from django.shortcuts import render
import stripe
from django.conf import settings
import os

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")


@csrf_exempt
def stripe_webhook(request):
    payload = request.body
    sig_header = request.META.get('HTTP_STRIPE_SIGNATURE')
    endpoint_secret = settings.STRIPE_WEBHOOK_SECRET

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, endpoint_secret
        )
    except ValueError as e:
        return HttpResponse(status=400)
    except stripe.error.SignatureVerificationError as e:
        return HttpResponse(status=400)

    # ✅ Check for successful payment
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        print("✅ Payment successful!")
        print("Session ID:", session['id'])
        print("Customer Email:", session.get('customer_email'))

        # ✅ Your logic: Activate user, create subscription, etc.

    return HttpResponse(status=200)


def payment_success(request):
    session_id = request.GET.get('session_id')
    return render(request, 'payment_success.html', {'session_id': session_id})


def payment_cancel(request):
    return render(request, 'payment_cancel.html')
