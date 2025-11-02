# banks/views_portal.py
from django.db.models import Max
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.http import HttpResponse
from django.utils import timezone

from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from drf_spectacular.utils import (
    extend_schema, OpenApiParameter, OpenApiResponse, OpenApiExample
)

from accounts.models import User
from assessments.models import Assessment
from questionnaires.models import LoanEligibilityResult, LoanRequest

from banks.permissions import IsBankUser
from banks.serializers import (
    BankSPOListItemSerializer, BankSPODetailSerializer, BankSPODetailAssessmentSerializer
)

class BankSPOViewSet(viewsets.ViewSet):
    """
    BANK_USER visibility: **all SPOs system-wide**
    """
    permission_classes = [IsBankUser]

    def _base_qs(self):
        return User.objects.filter(role=User.Role.SPO).select_related("organization")

    # ---------------------- LIST ----------------------
    @extend_schema(
        tags=["Bank • SPOs"],
        operation_id="bank_spo_list",
        summary="List SPOs (system-wide)",
        description=(
            "Returns paginated list of SPOs for bank users.\n\n"
            "**Columns:**\n"
            "- `id`: SPO user id (sortable by `ordering=id` or `-id`)\n"
            "- `date_joined` (also filterable via `joined_from`, `joined_to`)\n"
            "- `organization_name`, `focus_sector` (from Organization)\n"
            "- `org_created_at` (if model has it; else null)\n"
            "- `last_assessment_submitted_at`, `last_loan_request_submitted_at`\n\n"
            "**Filters** (all optional):\n"
            "- `q`: search email/name/organization\n"
            "- `sector`: exact match on organization.focus_area (exposed as `focus_sector`)\n"
            "- `is_active`: true/false\n"
            "- `joined_from`, `joined_to`: ISO datetime\n"
            "- `ordering`: `id` | `-id` (default `-id`)\n"
            "- `limit`, `offset`: pagination (default limit=50)\n"
        ),
        parameters=[
            OpenApiParameter(name="q", required=False, type=str),
            OpenApiParameter(name="sector", required=False, type=str),
            OpenApiParameter(name="is_active", required=False, type=str, description="true|false"),
            OpenApiParameter(name="joined_from", required=False, type=str),
            OpenApiParameter(name="joined_to", required=False, type=str),
            OpenApiParameter(name="ordering", required=False, type=str, description="id or -id"),
            OpenApiParameter(name="limit", required=False, type=int, description="Default 50"),
            OpenApiParameter(name="offset", required=False, type=int, description="Default 0"),
        ],
        responses={200: BankSPOListItemSerializer(many=True)},
        examples=[
            OpenApiExample(
                "Row example",
                value=[{
                    "id": 12345,
                    "email": "spo@startup.com",
                    "first_name": "Asha",
                    "last_name": "Verma",
                    "is_active": True,
                    "date_joined": "2025-09-30T10:15:00Z",
                    "organization_name": "GreenTech Pvt",
                    "focus_sector": "Health",
                    "org_created_at": None,
                    "last_assessment_submitted_at": "2025-10-20T09:00:00Z",
                    "last_loan_request_submitted_at": "2025-10-21T11:30:00Z"
                }]
            )
        ]
    )
    def list(self, request):
        qs = self._base_qs()

        # filters
        q = request.query_params.get("q")
        if q:
            qs = qs.filter(
                (User._meta.model.objects.filter(pk__in=qs.values("id"))).filter(
                    # compound via icontains on email/first/last/org
                )
            )
            # ^ DRF won't like this; just a placeholder to indicate we will OR the predicates below.

        # build OR directly:
        from django.db.models import Q
        if q:
            qs = qs.filter(
                Q(email__icontains=q) |
                Q(first_name__icontains=q) |
                Q(last_name__icontains=q) |
                Q(organization__name__icontains=q)
            )

        sector = request.query_params.get("sector")
        if sector:
            qs = qs.filter(organization__focus_sector=sector)

        is_active = request.query_params.get("is_active")
        if is_active in ("true", "false"):
            qs = qs.filter(is_active=(is_active == "true"))

        jf = request.query_params.get("joined_from")
        jt = request.query_params.get("joined_to")
        if jf:
            qs = qs.filter(date_joined__gte=jf)
        if jt:
            qs = qs.filter(date_joined__lte=jt)

        ordering = request.query_params.get("ordering") or "-id"
        if ordering not in ("id", "-id"):
            ordering = "-id"
        qs = qs.order_by(ordering)

        # pagination (limit/offset) default 50
        try:
            limit = int(request.query_params.get("limit", 50))
        except ValueError:
            limit = 50
        try:
            offset = int(request.query_params.get("offset", 0))
        except ValueError:
            offset = 0

        ids = list(qs.values_list("id", flat=True)[offset:offset+limit])
        rows = []
        # prefetch summary timestamps
        submitted_by_user = (
            Assessment.objects.filter(organization__created_by_id__in=ids, status="SUBMITTED")
            .values("organization__created_by_id")
            .annotate(last_submitted=Max("submitted_at"))
        )
        last_a_map = {x["organization__created_by_id"]: x["last_submitted"] for x in submitted_by_user}

        last_lr = (
            LoanRequest.objects.filter(organization__created_by_id__in=ids)
            .values("organization__created_by_id")
            .annotate(last_lr=Max("submitted_at"))
        )
        last_lr_map = {x["organization__created_by_id"]: x["last_lr"] for x in last_lr}

        for u in User.objects.filter(id__in=ids).select_related("organization"):
            org = getattr(u, "organization", None)
            rows.append({
                "id": u.id,
                "email": u.email,
                "first_name": u.first_name,
                "last_name": u.last_name,
                "is_active": u.is_active,
                "date_joined": u.date_joined,
                "organization_name": getattr(org, "name", "") or "",
                "focus_sector": getattr(org, "focus_sector", "") or "",
                "org_created_at": getattr(org, "created_at", None) if hasattr(org, "created_at") else None,
                "last_assessment_submitted_at": last_a_map.get(u.id),
                "last_loan_request_submitted_at": last_lr_map.get(u.id),
            })

        return Response({
            "count": self._base_qs().count(),
            "results": BankSPOListItemSerializer(rows, many=True).data
        })

    # ---------------------- RETRIEVE ----------------------
    @extend_schema(
        tags=["Bank • SPOs"],
        operation_id="bank_spo_detail",
        summary="SPO detail (organization + assessments)",
        description=(
            "Detail page for a single SPO user id. Includes Organization info and assessments. "
            "Each assessment is merged with its latest eligibility snapshot and latest loan request id (if present)."
        ),
        responses={200: BankSPODetailSerializer, 404: OpenApiResponse(description="Not found")},
        examples=[
            OpenApiExample(
                "Detail example (trimmed)",
                value={
                    "spo": {
                        "id": 12345, "email": "spo@startup.com",
                        "first_name": "Asha", "last_name": "Verma",
                        "is_active": True, "date_joined": "2025-09-30T10:15:00Z"
                    },
                    "organization": {
                        "name": "GreenTech Pvt", "registration_type": "PRIVATE_LTD",
                        "cin": "U123...", "focus_sector": "Health",
                        "poc_email": "founder@x.com"
                    },
                    "assessments": [
                        {
                            "id": 88, "status": "SUBMITTED",
                            "started_at": "2025-10-01T09:00:00Z",
                            "submitted_at": "2025-10-01T10:00:00Z",
                            "scores": {"overall": 72, "sections": {"IMPACT": 90, "RISK": 20, "RETURN": 90}},
                            "eligibility_overall": 72.0, "eligibility_decision": True, "eligibility_reason": None,
                            "loan_request_id": 7
                        }
                    ],
                    "email_placeholder": ""
                }
            )
        ]
    )
    def retrieve(self, request, pk=None):
        spo = get_object_or_404(User.objects.select_related("organization"), pk=pk, role=User.Role.SPO)
        org = getattr(spo, "organization", None)

        # all assessments under this organization
        assessments = Assessment.objects.filter(organization=org).order_by("-submitted_at", "-started_at")
        # pre-map eligibilities and latest loan request per assessment
        elig_map = {
            e.assessment_id: e for e in LoanEligibilityResult.objects.filter(assessment__in=assessments)
        }
        lr_map = (
            LoanRequest.objects.filter(assessment__in=assessments)
            .values("assessment_id").annotate(last_lr_id=Max("id"))
        )
        lr_by_assessment = {row["assessment_id"]: row["last_lr_id"] for row in lr_map}

        items = []
        for a in assessments:
            elig = elig_map.get(a.id)
            items.append({
                "id": a.id,
                "status": a.status,
                "started_at": a.started_at,
                "submitted_at": a.submitted_at,
                "scores": a.scores or {},
                "eligibility_overall": float(elig.overall_score) if elig else None,
                "eligibility_decision": bool(elig.is_eligible) if elig else None,
                "eligibility_reason": (elig.details or {}).get("reason") if elig else None,
                "loan_request_id": lr_by_assessment.get(a.id),
            })

        payload = {
            "spo": {
                "id": spo.id,
                "email": spo.email,
                "first_name": spo.first_name,
                "last_name": spo.last_name,
                "is_active": spo.is_active,
                "date_joined": spo.date_joined,
            },
            "organization": {
                "name": getattr(org, "name", "") if org else "",
                "registration_type": getattr(org, "registration_type", "") if org else "",
                "cin": getattr(org, "cin_number", None) if org else None,
                "focus_sector": getattr(org, "focus_sector", "") if org else "",
                "poc_email": spo.email,
            },
            "assessments": BankSPODetailAssessmentSerializer(items, many=True).data,
            "email_placeholder": "",
        }
        return Response(payload)

    # ---------------------- REPORT (PDF) ----------------------
    @extend_schema(
        tags=["Bank • SPOs"],
        operation_id="bank_spo_report_pdf",
        summary="Download SPO report (PDF) for Bank",
        description="Bank-scoped PDF with SPO, Organization, Assessment scores & eligibility.",
        responses={200: OpenApiResponse(description="PDF binary")},
    )
    @action(detail=True, methods=["get"], url_path="report")
    def report(self, request, pk=None):
        spo = get_object_or_404(User.objects.select_related("organization"), pk=pk, role=User.Role.SPO)
        org = getattr(spo, "organization", None)
        assessments = Assessment.objects.filter(organization=org).order_by("-submitted_at", "-started_at")

        # prebuild list merged with elig
        elig_map = {e.assessment_id: e for e in LoanEligibilityResult.objects.filter(assessment__in=assessments)}
        rows = []
        for a in assessments:
            e = elig_map.get(a.id)
            rows.append({
                "id": a.id,
                "status": a.status,
                "started_at": a.started_at,
                "submitted_at": a.submitted_at,
                "scores": a.scores or {},
                "eligibility": {
                    "overall": float(e.overall_score) if e else None,
                    "decision": bool(e.is_eligible) if e else None,
                    "reason": (e.details or {}).get("reason") if e else None,
                }
            })

        html = render_to_string("bank_portal/spo_report.html", {
            "generated_at": timezone.now(),
            "spo": spo,
            "org": org,
            "assessments": rows,
        })
        from weasyprint import HTML as WEASY_HTML
        pdf_bytes = WEASY_HTML(string=html).write_pdf()

        resp = HttpResponse(pdf_bytes, content_type="application/pdf")
        resp["Content-Disposition"] = f'attachment; filename="bank-spo-{spo.id}.pdf"'
        return resp