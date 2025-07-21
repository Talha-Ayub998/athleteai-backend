from django.urls import path
from .views import RegisterView, LoginView, \
                    LogoutView, CustomTokenRefreshView, \
                    CreateCheckoutSessionView, ListUsersView
from users.webhooks import *

urlpatterns = [
    path('register/', RegisterView.as_view(), name='register'),
    path('user-list/', ListUsersView.as_view(), name='user-list'),
    path('login/', LoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('token/refresh/', CustomTokenRefreshView.as_view(), name='token_refresh'),
    path('create-checkout-session/', CreateCheckoutSessionView.as_view(), name='create-checkout-session'),
]

urlpatterns += [
    path('stripe/webhook/', stripe_webhook, name='stripe-webhook'),
    path('success/', payment_success, name='payment-success'),
    path('cancel/', payment_cancel, name='payment-cancel'),
]
