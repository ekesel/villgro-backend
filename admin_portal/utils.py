from datetime import date, datetime
from django.utils import timezone

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