# admin_portal/views_dashboard.py
from datetime import datetime, timedelta
from typing import Dict, Any, Tuple, List

from django.utils import timezone
from django.db.models import Count
from django.core.cache import cache

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiResponse

from accounts.models import User
from organizations.models import Organization, OnboardingProgress
from assessments.models import Assessment
from assessments.services import compute_progress
from admin_portal.permissions import IsAdminRole
from admin_portal.models import ActivityLog


def _window_from_query(params) -> Tuple[datetime, datetime]:
    """
    Global filter: prefer explicit from/to (ISO 8601), else use ?days=N (default 7).
    All in Django TZ (UTC in our project).
    """
    tz_now = timezone.now()
    date_from = params.get("from")
    date_to = params.get("to")
    if date_from and date_to:
        try:
            start = datetime.fromisoformat(date_from)
            end = datetime.fromisoformat(date_to)
            if timezone.is_naive(start):
                start = timezone.make_aware(start, timezone=timezone.utc)
            if timezone.is_naive(end):
                end = timezone.make_aware(end, timezone=timezone.utc)
            return (start, end)
        except Exception:
            # fall back to days below if parse fails
            pass
    days = int(params.get("days", 7) or 7)
    start = tz_now - timedelta(days=days)
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
            # cache per querystring for 60s
            cache_key = f"admin-dashboard:{request.META.get('QUERY_STRING','')}"
            cached = cache.get(cache_key)
            if cached:
                return Response(cached)

            win_from, win_to = _window_from_query(request.query_params)

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
            loan_requests = 0

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
            orgs = Organization.objects.filter(
                created_by_id__in=active_spo_ids,
                focus_sector__isnull=False,
            )
            sector_counts = (
                orgs.values("focus_sector")
                    .annotate(count=Count("id"))
                    .order_by()
            )
            total_orgs = sum(row["count"] for row in sector_counts) or 1
            sector_distribution = [
                {
                    "key": row["focus_sector"],
                    "count": row["count"],
                    "percent": round(_safe_div(row["count"], total_orgs) * 100.0, 2),
                }
                for row in sector_counts
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

            cache.set(cache_key, data, 60)
            return Response(data)
        except Exception as e:
            return Response(
                {"message": "We could not fetch the dashboard data right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )