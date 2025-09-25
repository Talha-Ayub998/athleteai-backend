from django.urls import path
from .views import RegisterView, LoginView, \
                    LogoutView, CustomTokenRefreshView, \
                    CreateCheckoutSessionView, ListUsersView, \
                    CurrentUserView, CancelSubscriptionView, \
                    CurrentSubscriptionView, ContactUsView, \
                    CurrentLimitsView
from users.webhooks import *

urlpatterns = [
    path('register/', RegisterView.as_view(), name='register'),
    path("contact-us/", ContactUsView.as_view(), name="contact-us"),
    path('user-list/', ListUsersView.as_view(), name='user-list'),
    path('me/', CurrentUserView.as_view(), name='current-user'),
    path('login/', LoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path("limits/", CurrentLimitsView.as_view(), name="current-limits"),
    path('token/refresh/', CustomTokenRefreshView.as_view(), name='token_refresh'),
    path('create-checkout-session/', CreateCheckoutSessionView.as_view(), name='create-checkout-session'),
]

urlpatterns += [
    path('stripe/webhook/', stripe_webhook, name='stripe-webhook'),
    path('subscription/cancel/', CancelSubscriptionView.as_view(), name='subscription-cancel'),
    path('my-subscription/', CurrentSubscriptionView.as_view(), name='subscription-details'),
    path('success/', payment_success, name='payment-success'),
    path('cancel/', payment_cancel, name='payment-cancel'),
]
