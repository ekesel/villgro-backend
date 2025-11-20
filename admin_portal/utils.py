from admin_portal.models import AuditLog
from datetime import date, datetime
from django.utils import timezone

def audit(actor, action, target, before=None, after=None):
    AuditLog.objects.create(
        actor=actor,
        action=action,
        target_model=target.__class__.__name__,
        target_id=str(getattr(target, "pk", "")),
        changes={"before": before or {}, "after": after or {}},
    )

def _format_human_datetime(value):
    """
    Convert date/datetime to nice readable string for audit logs.
    Keeps other types unchanged.
    """
    if isinstance(value, datetime):
        try:
            # convert to local time if it's aware
            if timezone.is_aware(value):
                value = timezone.localtime(value)
        except Exception:
            pass
        # e.g. "19 Nov 2025, 05:46 PM"
        return value.strftime("%d %b %Y, %I:%M %p")
    if isinstance(value, date):
        # e.g. "19 Nov 2025"
        return value.strftime("%d %b %Y")
    return value