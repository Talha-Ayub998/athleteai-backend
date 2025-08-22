# Standard Library
import os

# Django
from django.views import View
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.utils.timezone import now

# Django REST Framework
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

# Swagger / drf-yasg
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from django.contrib.auth.models import AnonymousUser

# App-specific imports
from users.models import CustomUser, Subscription
from .serializers import (
    RegisterSerializer,
    LoginSerializer,
    LogoutSerializer,
    UserSerializer,
    UserListSerializer,
)
from .stripe_prices import STRIPE_PRICES

import json
from athleteai.permissions import BlockSuperUserPermission, IsAdminOnly

# Stripe configuration
import stripe
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

class RegisterView(APIView):
    permission_classes = [AllowAny, BlockSuperUserPermission]
    @swagger_auto_schema(request_body=RegisterSerializer)
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            refresh = RefreshToken.for_user(user)
            return Response({
                "user": UserSerializer(user).data,
                "refresh": str(refresh),
                "access": str(refresh.access_token),
            }, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class ListUsersView(APIView):
    permission_classes = [IsAuthenticated, BlockSuperUserPermission, IsAdminOnly]

    @swagger_auto_schema(
        operation_description="Admins can view all athletes. Superusers and athletes are not allowed.",
        responses={
            200: openapi.Response(description="List of users"),
            403: "Forbidden",
            500: "Failed to fetch user list",
        }
    )
    def get(self, request):
        try:
            # ✅ This is now guaranteed to be an admin
            users = CustomUser.objects.filter(role='athlete').order_by('-date_joined')
            serialized = UserListSerializer(users, many=True)
            return Response(serialized.data, status=200)

        except Exception as e:
            print(f"User list error: {e}")
            return Response(
                {"error": "Failed to fetch user list."},
                status=500
            )


class LoginView(APIView):
    permission_classes = [AllowAny, BlockSuperUserPermission]

    @swagger_auto_schema(request_body=LoginSerializer)
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.validated_data["user"]
            tokens = serializer.validated_data["tokens"]

            if user.is_superuser or user.role == 'superuser':
                return Response({"error": "You do not have permission to perform this action."}, status=403)

            # ✅ Manually update last_login timestamp
            user.last_login = now()
            user.save(update_fields=["last_login"])

            return Response({
                "user": {
                    "id": user.id,
                    "email": user.email,
                    "username": user.username,
                    "role": user.role,
                    "last_login": user.last_login,
                    "date_joined": user.date_joined
                },
                "access": tokens["access"],
                "refresh": tokens["refresh"]
            })
        return Response(serializer.errors, status=400)

class LogoutView(APIView):
    permission_classes = [IsAuthenticated, BlockSuperUserPermission]

    @swagger_auto_schema(request_body=LogoutSerializer)
    def post(self, request):
        refresh_token = request.data.get("refresh")
        if not refresh_token:
            return Response({"error": "Refresh token is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            token = RefreshToken(refresh_token)
            token.blacklist()
            return Response({"detail": "Logged out successfully"}, status=status.HTTP_205_RESET_CONTENT)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class CurrentUserView(APIView):
    permission_classes = [IsAuthenticated, BlockSuperUserPermission]

    def get(self, request):
        user = request.user
        serializer = UserSerializer(user)
        return Response(serializer.data, status=200)

class CustomTokenRefreshView(TokenRefreshView):
    """
    Custom view to refresh an access token using a valid refresh token.
    """
    permission_classes = [BlockSuperUserPermission]

    @swagger_auto_schema(
        operation_description="Get a new access token using a valid refresh token.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=["refresh"],
            properties={
                "refresh": openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description="Valid refresh token",
                ),
            },
        ),
        responses={
            200: openapi.Response(
                description="New access token",
                examples={
                    "application/json": {
                        "access": "new_access_token"
                    }
                }
            ),
            401: "Invalid or expired refresh token"
        }
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)



class CreateCheckoutSessionView(APIView):
    permission_classes = [IsAuthenticated, BlockSuperUserPermission]

    @swagger_auto_schema(
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=["type", "plan"],
            properties={
                "type": openapi.Schema(type=openapi.TYPE_STRING, enum=["subscription", "one_time", "free"]),
                "plan": openapi.Schema(type=openapi.TYPE_STRING, enum=["essentials", "precision", "pdf_report", "free"]),
                "interval": openapi.Schema(type=openapi.TYPE_STRING, enum=["month", "year"]),
            }
        ),
        responses={200: "Checkout URL or success detail", 400: "Validation error", 401: "Unauthorized"}
    )
    def post(self, request):
        user = request.user
        flow_type = request.data.get("type")
        plan = request.data.get("plan")
        interval = request.data.get("interval")  # only for subscriptions

        # Free plan (no Stripe call)
        if flow_type == "free" or plan == "free":
            sub, _ = Subscription.objects.get_or_create(user=user)
            sub.plan = "free"
            sub.interval = None
            sub.status = "active"
            sub.stripe_subscription_id = None
            sub.save(update_fields=["plan", "interval", "status", "stripe_subscription_id"])
            return Response({"detail": "Free plan activated"}, status=status.HTTP_200_OK)

        # Ensure Stripe customer
        sub, _ = Subscription.objects.get_or_create(user=user)
        if sub.stripe_customer_id:
            customer_id = sub.stripe_customer_id
        else:
            customer = stripe.Customer.create(email=user.email)
            customer_id = customer.id
            sub.stripe_customer_id = customer_id
            sub.save(update_fields=["stripe_customer_id"])

        success_url = 'https://54.215.71.202.nip.io/api/users/success/?session_id={CHECKOUT_SESSION_ID}'
        cancel_url = 'https://54.215.71.202.nip.io/api/users/cancel/'

        # Subscription flow
        if flow_type == "subscription":
            if plan not in ("essentials", "precision"):
                return Response({"error": "Invalid plan"}, status=status.HTTP_400_BAD_REQUEST)
            if interval not in ("month", "year"):
                return Response({"error": "Missing or invalid interval"}, status=status.HTTP_400_BAD_REQUEST)

            key = f"{plan}_{interval}"
            price_id = STRIPE_PRICES.get(key)
            if not price_id:
                return Response({"error": "Invalid plan/interval"}, status=status.HTTP_400_BAD_REQUEST)

            try:
                session = stripe.checkout.Session.create(
                    customer=customer_id,
                    mode='subscription',
                    line_items=[{"price": price_id, "quantity": 1}],
                    allow_promotion_codes=True,
                    success_url=success_url,
                    cancel_url=cancel_url,
                    metadata={"user_id": str(user.id), "plan": plan, "interval": interval},
                    client_reference_id=str(user.id),
                )
                return Response({"checkout_url": session.url}, status=status.HTTP_200_OK)
            except stripe.error.StripeError as e:
                return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        # One-time PDF flow
        if flow_type == "one_time" and plan == "pdf_report":
            price_id = STRIPE_PRICES.get("pdf_report")
            if not price_id:
                return Response({"error": "Price not configured"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            try:
                session = stripe.checkout.Session.create(
                    customer=customer_id,
                    mode='payment',
                    line_items=[{"price": price_id, "quantity": 1}],
                    success_url=success_url,
                    cancel_url=cancel_url,
                    metadata={"user_id": str(user.id), "plan": plan},
                    client_reference_id=str(user.id),
                )
                return Response({"checkout_url": session.url}, status=status.HTTP_200_OK)
            except stripe.error.StripeError as e:
                return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response({"error": "Invalid request"}, status=status.HTTP_400_BAD_REQUEST)
