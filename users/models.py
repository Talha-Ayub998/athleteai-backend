from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils.translation import gettext_lazy as _


class CustomUserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('The Email field is required')
        email = self.normalize_email(email).lower() 
        extra_fields.setdefault('username', email.split('@')[0])
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('username', email.split('@')[0])
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('role', 'superuser')
        return self.create_user(email, password, **extra_fields)


class CustomUser(AbstractUser):
    ROLE_CHOICES = (
        ('superuser', 'Superuser'),
        ('admin', 'Admin'),
        ('athlete', 'Athlete'),
    )

    username = models.CharField(max_length=150, blank=True)
    email = models.EmailField(_('email address'), unique=True)

    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='athlete')

    # Admins can manage multiple users (many-to-many, asymmetrical)
    managed_users = models.ManyToManyField(
        'self',
        blank=True,
        symmetrical=False,
        related_name='managed_by'
    )

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []  # no need for username

    objects = CustomUserManager()

    def __str__(self):
        return self.email

    def is_superuser_role(self):
        return self.role == 'superuser' or self.is_superuser

    def is_admin_role(self):
        return self.role == 'admin'

    def is_athlete_role(self):
        return self.role == 'athlete'

class Subscription(models.Model):
    PLAN_CHOICES = (
        ('free', 'Free'),
        ('essentials', 'Essentials'),
        ('precision', 'Precision'),
    )
    INTERVAL_CHOICES = (('month', 'Month'), ('year', 'Year'))

    user = models.OneToOneField('users.CustomUser', on_delete=models.CASCADE, related_name='subscription')
    plan = models.CharField(max_length=20, choices=PLAN_CHOICES, default='free')
    interval = models.CharField(max_length=10, choices=INTERVAL_CHOICES, null=True, blank=True)
    stripe_customer_id = models.CharField(max_length=100, blank=True, null=True)
    stripe_subscription_id = models.CharField(max_length=100, blank=True, null=True)
    status = models.CharField(max_length=30, default='inactive')  # active, trialing, past_due, canceled, incomplete
    current_period_end = models.DateTimeField(null=True, blank=True)
    cancel_at_period_end = models.BooleanField(default=False)

class ReportPurchase(models.Model):
    user = models.ForeignKey('users.CustomUser', on_delete=models.CASCADE, related_name='report_purchases')
    stripe_payment_intent = models.CharField(max_length=100)
    amount = models.IntegerField()  # in cents
    created_at = models.DateTimeField(auto_now_add=True)
    # optionally store a usage/entitlement flag (e.g., downloads remaining)

class ContactMessage(models.Model):
    name = models.CharField(max_length=150)
    email = models.EmailField()
    description = models.TextField()

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} <{self.email}>"