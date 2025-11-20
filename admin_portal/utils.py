from datetime import date, datetime
from django.utils import timezone

from django.utils.dateparse import parse_datetime, parse_date

def _format_human_datetime(value):
    """
    Convert datetime/date OR string-represented datetime/date
    into human-readable format for audit logs.
    """
    if value is None:
        return None

    # --- If already datetime ---
    if isinstance(value, datetime):
        return _fmt_dt(value)

    # --- If already date ---
    if isinstance(value, date):
        return value.strftime("%d %b %Y")

    # --- If string, try datetime parse ---
    if isinstance(value, str):
        # 1) Try full datetime
        dt = parse_datetime(value)
        if dt:
            return _fmt_dt(dt)

        # 2) Try date-only
        d = parse_date(value)
        if d:
            return d.strftime("%d %b %Y")

    # Fallback → return original
    return value


def _fmt_dt(dt):
    """Helper for formatting a datetime."""
    try:
        if timezone.is_aware(dt):
            dt = timezone.localtime(dt)
    except Exception:
        pass
    return dt.strftime("%d %b %Y, %I:%M %p")