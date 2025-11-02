# banks/permissions.py
from rest_framework.permissions import BasePermission

class IsBankUser(BasePermission):
    """
    Allow only users with role=BANK_USER
    """
    def has_permission(self, request, view):
        u = getattr(request, "user", None)
        return bool(u and u.is_authenticated and getattr(u, "role", None) == "BANK_USER")