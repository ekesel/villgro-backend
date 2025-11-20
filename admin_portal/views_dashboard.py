# admin_portal/views_dashboard.py
from datetime import datetime, timedelta, time
from typing import Dict, Any, Tuple, List
import logging
from django.utils import timezone
from django.db.models import Count
from django.core.cache import cache

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiResponse
from django.utils.dateparse import parse_datetime, parse_date
from accounts.models import User
from organizations.models import Organization, OnboardingProgress
from assessments.models import Assessment
from assessments.services import compute_progress
from admin_portal.permissions import IsAdminRole
from admin_portal.models import ActivityLog
from questionnaires.models import LoanRequest

logger = logging.getLogger(__name__)

def _window_from_query(params) -> Tuple[datetime, datetime]:
    """
    Global filter: prefer explicit from/to (ISO 8601), else use ?days=N (default 7).

    Supports:
      - full datetimes: 2025-10-01T00:00:00 or 2025-10-01 00:00:00
      - date-only:      2025-10-01 (interpreted as start_of_day / end_of_day)
      - timezone suffixes like 'Z' or '+05:30' (handled by parse_datetime)

    All returned datetimes are timezone-aware (using Django's default timezone).
    """
    tz_now = timezone.now()
    date_from = params.get("from")
    date_to = params.get("to")

    logger.info(
        "AdminDashboardSummaryView: raw window params from=%r to=%r",
        date_from, date_to
    )

    if date_from and date_to:
        try:
            # 1) Try full datetime
            start = parse_datetime(date_from)
            end = parse_datetime(date_to)

            # 2) Fallback: date-only like '2025-10-01'
            if start is None:
                d_from = parse_date(date_from)
                if d_from is not None:
                    start = datetime.combine(d_from, time.min)

            if end is None:
                d_to = parse_date(date_to)
                if d_to is not None:
                    end = datetime.combine(d_to, time.max)

            logger.info(
                "AdminDashboardSummaryView: parsed window start=%r end=%r",
                start, end
            )

            if start and end:
                if timezone.is_naive(start):
                    start = timezone.make_aware(start)  # uses default TIME_ZONE
                if timezone.is_naive(end):
                    end = timezone.make_aware(end)
                return (start, end)
        except Exception as exc:
            logger.warning(
                "AdminDashboardSummaryView: failed to parse from/to (%r, %r): %s",
                date_from, date_to, exc
            )

    # Fallback: last N days
    days = int(params.get("days", 7) or 7)
    start = tz_now - timedelta(days=days)
    logger.info(
        "AdminDashboardSummaryView: using fallback window days=%s -> %s .. %s",
        days, start, tz_now
    )
    return (start, tz_now)


def _safe_div(a: float, b: float) -> float:
    # returns percentage 0–100 with 2 decimals
    return round((a / b * 100.0) if b else 0.0, 2)


@extend_schema(
    tags=["Admin • Dashboard"],
    summary="Admin dashboard summary (global date filter applies to all widgets)",
    parameters=[
        OpenApiParameter(
            name="days",
            description="Lookback window in days (ignored if 'from' and 'to' provided). Default 7.",
            required=False, type=int
        ),
        OpenApiParameter(
            name="from",
            description="ISO datetime (UTC). Example: 2025-10-25T00:00:00",
            required=False, type=str
        ),
        OpenApiParameter(
            name="to",
            description="ISO datetime (UTC). Example: 2025-10-28T23:59:59",
            required=False, type=str
        ),
    ],
    responses={
        200: OpenApiResponse(description="Aggregated dashboard metrics for the selected window"),
    },
)
class AdminDashboardSummaryView(APIView):
    """
    Single endpoint that returns:
      - KPI cards (total_spos, new_spos, completion_rate, loan_requests=0)
      - Assessment completion funnel (counts + percents + denominators)
      - Sector distribution (focus_sector)
      - Recent activity (empty for now, per SOW)
    All metrics respect the global date window.
    """
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request):
        try:
            # # cache per querystring for 60s
            # cache_key = f"admin-dashboard:{request.META.get('QUERY_STRING','')}"
            # cached = cache.get(cache_key)
            # if cached:
            #     return Response(cached)

            win_from, win_to = _window_from_query(request.query_params)
            logger.info(f"AdminDashboardSummaryView: window from {win_from} to {win_to}")

            # ---------- KPI: SPOs (windowed) ----------
            spos_qs = User.objects.filter(
                role=User.Role.SPO,
                is_active=True,
                date_joined__gte=win_from,
                date_joined__lte=win_to,
            )
            total_spos = spos_qs.count()
            new_spos = total_spos  # in this design, total is also windowed

            # ---------- KPI: Completion rate (submitted / started in window) ----------
            started_qs = Assessment.objects.filter(
                started_at__gte=win_from, started_at__lte=win_to
            )
            submitted_qs = Assessment.objects.filter(
                submitted_at__gte=win_from, submitted_at__lte=win_to
            )
            completion_rate = _safe_div(submitted_qs.count(), started_qs.count())

            # ---------- KPI: Loan requests (placeholder = 0) ----------
            loan_requests = LoanRequest.objects.filter(
                submitted_at__gte=win_from,
                submitted_at__lte=win_to,
            ).count()

            # ---------- Funnel ----------
            # Registered (windowed)
            funnel_registered = total_spos

            # Completed basic info (prefer OnboardingProgress)
            try:
                completed_basic = OnboardingProgress.objects.filter(
                    current_step__gte=2,
                    updated_at__gte=win_from, updated_at__lte=win_to,
                    user__role=User.Role.SPO,
                    user__is_active=True,
                    user__date_joined__gte=win_from,
                    user__date_joined__lte=win_to,
                ).count()
            except Exception:
                # Fallback: orgs with step-2 fields and creator in window
                completed_basic = Organization.objects.filter(
                    created_by__role=User.Role.SPO,
                    created_by__is_active=True,
                    created_by__date_joined__gte=win_from,
                    created_by__date_joined__lte=win_to,
                    type_of_innovation__isnull=False,
                ).count()

            # Completed sections (IMPACT, RISK, RETURN) regardless of submit
            assessments = list(
                Assessment.objects.filter(started_at__gte=win_from, started_at__lte=win_to)
                .select_related("organization")
            )
            sec_codes = ["IMPACT", "RISK", "RETURN"]
            completed_sections_counts = {c: 0 for c in sec_codes}
            for a in assessments:
                prog = compute_progress(a)  # current answers
                by_sec = prog.get("by_section", {})
                for sc in sec_codes:
                    stats = by_sec.get(sc) or {}
                    if stats.get("required", 0) and stats.get("answered", 0) >= stats.get("required", 0):
                        completed_sections_counts[sc] += 1

            # Denominators and percents
            denom_registered = max(funnel_registered, 1)
            denom_assess = max(len(assessments), 1)
            funnel = {
                "counts": {
                    "registered": funnel_registered,
                    "completed_basic_info": completed_basic,
                    "completed_impact": completed_sections_counts["IMPACT"],
                    "completed_risk": completed_sections_counts["RISK"],
                    "completed_return": completed_sections_counts["RETURN"],
                },
                "percents": {
                    "registered": _safe_div(funnel_registered, denom_registered),  # 100 if >0
                    "completed_basic_info": _safe_div(completed_basic, denom_registered),
                    "completed_impact": _safe_div(completed_sections_counts["IMPACT"], denom_assess),
                    "completed_risk": _safe_div(completed_sections_counts["RISK"], denom_assess),
                    "completed_return": _safe_div(completed_sections_counts["RETURN"], denom_assess),
                },
                "denominators": {
                    "registered": funnel_registered,
                    "sections": len(assessments),
                }
            }

            # ---------- Sector distribution ----------
            active_spo_ids = spos_qs.values_list("id", flat=True)

            # Fetch all organizations for active SPOs (including null sectors)
            orgs = Organization.objects.filter(
                created_by_id__in=active_spo_ids,
            )

            sector_counts_raw = (
                orgs.values("focus_sector")
                    .annotate(count=Count("id"))
                    .order_by()
            )

            # Prepare merged bucket list
            cleaned = []
            null_count = 0
            others_index = None

            for idx, row in enumerate(sector_counts_raw):
                key = row["focus_sector"]
                count = row["count"]

                if key is None:
                    null_count += count
                    continue

                if key.upper() == "OTHERS":
                    others_index = idx
                    cleaned.append({"key": "OTHERS", "count": count})
                else:
                    cleaned.append({"key": key, "count": count})

            # Merge NULL count into OTHERS
            if null_count > 0:
                if any(item["key"] == "OTHERS" for item in cleaned):
                    # Add nulls to existing OTHERS
                    for item in cleaned:
                        if item["key"] == "OTHERS":
                            item["count"] += null_count
                            break
                else:
                    # Create OTHERS bucket
                    cleaned.append({"key": "OTHERS", "count": null_count})

            # Final percent computation
            total_orgs = sum(item["count"] for item in cleaned) or 1
            sector_distribution = [
                {
                    "key": item["key"],
                    "count": item["count"],
                    "percent": _safe_div(item["count"], total_orgs),
                }
                for item in cleaned
            ]

            # ---------- Recent activity (empty per SOW for now) ----------
            recent_activity: List[Dict[str, Any]] = []
            recent = ActivityLog.objects.exclude(action="API_HIT").order_by("-created_at")[:10]
            if len(recent) > 0:
                recent_activity = [{
                    "id": r.id,
                    "timestamp": r.created_at.isoformat(),
                    "actor": getattr(r.actor, "email", None),
                    "action": r.action,
                    "object": f"{r.app_label}.{r.model}#{r.object_id}",
                    "help_text": r.help_text,
                } for r in recent]

            data = {
                "kpi": {
                    "total_spos": total_spos,
                    "new_spos": new_spos,
                    "completion_rate": completion_rate,
                    "loan_requests": loan_requests,
                    "window": {"from": win_from.isoformat(), "to": win_to.isoformat()},
                },
                "funnel": funnel,
                "sector_distribution": sector_distribution,
                "recent_activity": recent_activity,
            }

            # cache.set(cache_key, data, 60)
            return Response(data)
        except Exception as e:
            return Response(
                {"message": "We could not fetch the dashboard data right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )