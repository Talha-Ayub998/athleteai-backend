from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework.permissions import AllowAny
from drf_yasg.utils import swagger_auto_schema
from rest_framework_simplejwt.views import TokenRefreshView
from drf_yasg import openapi
from .serializers import LoginSerializer, UserSerializer,  RegisterSerializer, LogoutSerializer


class RegisterView(APIView):
    permission_classes = [AllowAny]
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


class LoginView(APIView):
    permission_classes = [permissions.AllowAny]

    @swagger_auto_schema(request_body=LoginSerializer)
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.validated_data["user"]
            tokens = serializer.validated_data["tokens"]
            return Response({
                "user": {
                    "id": user.id,
                    "email": user.email,
                    "is_admin": user.is_admin,
                },
                "access": tokens["access"],
                "refresh": tokens["refresh"]
            })
        return Response(serializer.errors, status=400)

class LogoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]

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