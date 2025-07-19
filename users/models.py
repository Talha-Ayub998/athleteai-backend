from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils.translation import gettext_lazy as _


class CustomUserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('The Email field is required')
        email = self.normalize_email(email)
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
