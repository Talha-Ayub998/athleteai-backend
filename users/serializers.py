from rest_framework import serializers
from django.contrib.auth import authenticate
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.password_validation import validate_password as django_validate_password
from django.core.exceptions import ValidationError as DjangoValidationError

from .models import CustomUser, ContactMessage

def custom_validate_password(password):
    try:
        django_validate_password(password)
    except DjangoValidationError:
        raise serializers.ValidationError(
            "Password must be at least 8 characters long and include letters, numbers, and special characters. Avoid common or numeric-only passwords."
        )

class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, validators=[custom_validate_password])
    password2 = serializers.CharField(write_only=True)

    # ✅ optional fields so website can pass plan info
    type = serializers.CharField(write_only=True, required=False, allow_blank=True)      # "subscription" | "one_time" | "free"
    plan = serializers.CharField(write_only=True, required=False, allow_blank=True)      # "essentials" | "precision" | "pdf_report" | "free"
    interval = serializers.CharField(write_only=True, required=False, allow_blank=True)  # "month" | "year"

    class Meta:
        model = CustomUser
        fields = ['username', 'email', 'password', 'password2', 'type', 'plan', 'interval']

    def validate(self, attrs):
        if attrs['password'] != attrs['password2']:
            raise serializers.ValidationError({"password": "Passwords do not match."})
        return attrs

    def create(self, validated_data):
        # strip helper-only fields; view will read them from request.data anyway
        validated_data.pop('password2', None)
        validated_data.pop('type', None)
        validated_data.pop('plan', None)
        validated_data.pop('interval', None)

        validated_data['role'] = 'athlete'  # keep your rule
        return CustomUser.objects.create_user(**validated_data)



class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, data):
        email = data.get("email", "").lower().strip()
        password = data.get("password")
        user = authenticate(email=email, password=password)

        if not user:
            raise serializers.ValidationError("Invalid email or password")

        refresh = RefreshToken.for_user(user)

        return {
            "user": user,
            "tokens": {
                "access": str(refresh.access_token),
                "refresh": str(refresh)
            }
        }

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomUser
        fields = ['id', 'username', 'email', 'role', 'last_login', 'date_joined']

class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField(help_text="Refresh token to blacklist")


class UserListSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomUser
        fields = ['id', 'username', 'email', 'role', 'last_login', 'date_joined']


class CurrentSubscriptionSerializer(serializers.Serializer):
    plan = serializers.CharField(allow_null=True)
    interval = serializers.CharField(allow_null=True)
    status = serializers.CharField(allow_null=True)
    cancel_at_period_end = serializers.BooleanField()
    current_period_end = serializers.DateTimeField(allow_null=True)  # ISO8601
    stripe_customer_id = serializers.CharField(allow_null=True)
    stripe_subscription_id = serializers.CharField(allow_null=True)
    # optional: expose remaining one-time report credits if you implemented it
    remaining_report_credits = serializers.IntegerField(required=False)

class ContactMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ContactMessage
        fields = ["id", "name", "email", "description", "created_at"]
