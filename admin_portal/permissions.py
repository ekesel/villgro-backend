from rest_framework.permissions import BasePermission

class IsAdminRole(BasePermission):
    """
    Accept any of:
      - Custom role == ADMIN (case-insensitive / enum-safe)
      - Django staff (is_staff)
      - Django superuser (is_superuser)
    """
    def has_permission(self, request, view):
        u = request.user
        if not (u and u.is_authenticated):
            return False

        # Try enum (User.Role.ADMIN) and string (e.g. "ADMIN")
        role_val = getattr(u, "role", None)
        is_role_admin = False
        try:
            # If your Role is an enum-like class on the model
            is_role_admin = (role_val == getattr(u, "Role").ADMIN)
        except Exception:
            pass
        # Also allow raw string comparison
        if not is_role_admin and isinstance(role_val, str):
            is_role_admin = role_val.strip().upper() == "ADMIN"

        return bool(is_role_admin or u.is_staff or u.is_superuser)