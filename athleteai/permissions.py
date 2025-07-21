# permissions.py

from rest_framework.permissions import BasePermission

class BlockSuperUserPermission(BasePermission):
    """
    Denies access if the user is a superuser or has role='superuser'
    """

    def has_permission(self, request, view):
        user = request.user
        return not (user and (user.is_superuser or getattr(user, "role", None) == "superuser"))


class IsAdminOnly(BasePermission):
    """
    Allows access only to users with the 'admin' role.
    """

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role == 'admin')
