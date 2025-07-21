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

# App-specific imports
from users.models import CustomUser
from .serializers import (
    RegisterSerializer,
    LoginSerializer,
    LogoutSerializer,
    UserSerializer,
    UserListSerializer,
)

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


from athleteai.permissions import IsAdminOnly

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

            # ✅ Manually update last_login timestamp
            user.last_login = now()
            user.save(update_fields=["last_login"])

            return Response({
                "user": {
                    "id": user.id,
                    "email": user.email,
                    "role": user.role,
                    "last_login": user.last_login
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



@method_decorator(csrf_exempt, name='dispatch')
class CreateCheckoutSessionView(View):
    def post(self, request):
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'unit_amount': 2000,
                    'product_data': {
                        'name': 'Signup Payment',
                    },
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url='https://54.215.71.202.nip.io/api/users/success/?session_id={CHECKOUT_SESSION_ID}',
            cancel_url='https://54.215.71.202.nip.io/api/users/cancel/',
        )
        return JsonResponse({'checkout_url': session.url})
