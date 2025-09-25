# users/admin.py
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import CustomUser, Subscription, ReportPurchase, ContactMessage

# ---------- Inlines ----------
class SubscriptionInline(admin.StackedInline):
    model = Subscription
    can_delete = False
    extra = 0
    # Make all operational fields read-only here to avoid edits in two places
    readonly_fields = (
        "plan", "interval", "status",
        "trial_start", "trial_end",
        "current_period_start", "current_period_end", "period_usage",
        "stripe_customer_id", "stripe_subscription_id", "cancel_at_period_end",
    )
    fields = (
        "plan", "interval", "status",
        "trial_start", "trial_end",
        "current_period_start", "current_period_end", "period_usage",
        "stripe_customer_id", "stripe_subscription_id", "cancel_at_period_end",
    )


class ReportPurchaseInline(admin.TabularInline):
    model = ReportPurchase
    extra = 0
    readonly_fields = ("stripe_payment_intent", "amount", "created_at", "consumed", "consumed_at")
    fields = ("stripe_payment_intent", "amount", "created_at", "consumed", "consumed_at")


# ---------- User ----------
@admin.register(CustomUser)
class CustomUserAdmin(BaseUserAdmin):
    list_display = ("email", "username", "role", "is_active", "is_staff", "is_superuser", "date_joined", "last_login")
    list_filter = ("role", "is_active", "is_staff", "is_superuser")
    search_fields = ("email", "username")
    ordering = ("email",)

    fieldsets = (
        (None, {"fields": ("email", "username", "password", "role")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Relations", {"fields": ("managed_users",)}),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",),
                "fields": ("email", "username", "password1", "password2", "role", "is_active", "is_staff")}),
    )

    inlines = [SubscriptionInline, ReportPurchaseInline]


# ---------- Subscriptions ----------
@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        "user", "plan", "interval", "status",
        "trial_end",
        "current_period_start", "current_period_end", "period_usage",
        "cancel_at_period_end",
    )
    list_filter = ("plan", "interval", "status", "cancel_at_period_end")
    search_fields = ("user__email", "stripe_customer_id", "stripe_subscription_id")
    autocomplete_fields = ("user",)
    readonly_fields = (
        "user", "plan", "interval", "status",
        "trial_start", "trial_end",
        "current_period_start", "current_period_end", "period_usage",
        "stripe_customer_id", "stripe_subscription_id", "cancel_at_period_end",
    )


# ---------- Report purchases ----------
@admin.register(ReportPurchase)
class ReportPurchaseAdmin(admin.ModelAdmin):
    list_display = ("user", "amount", "stripe_payment_intent", "consumed", "created_at")
    list_filter = ("consumed", "created_at")
    search_fields = ("user__email", "stripe_payment_intent")
    readonly_fields = ("stripe_payment_intent", "amount", "created_at", "consumed", "consumed_at")


# ---------- Contact messages ----------
@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "email", "short_description", "created_at")
    list_display_links = ("id", "name")
    search_fields = ("name", "email", "description")
    list_filter = ("created_at",)
    readonly_fields = ("created_at",)
    ordering = ("-created_at",)

    def short_description(self, obj):
        return (obj.description[:60] + "…") if len(obj.description) > 60 else obj.description
    short_description.short_description = "Description"
