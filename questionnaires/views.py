# questionnaires/views.py
from django.utils import timezone
from rest_framework import permissions, viewsets, status, mixins
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import serializers
from drf_spectacular.utils import (
    extend_schema, OpenApiParameter, OpenApiExample, inline_serializer
)

from assessments.models import Assessment
from organizations.models import Organization
from questionnaires.logic import eligibility_check
from questionnaires.models import LoanRequest
from questionnaires.serializers import (
    LoanMetaSerializer, LoanPrefillSerializer,
    LoanRequestCreateSerializer, LoanRequestDetailSerializer,
    fund_types_meta
)

class IsSPO(permissions.BasePermission):
    def has_permission(self, request, view):
        u = getattr(request, "user", None)
        return bool(u and u.is_authenticated and getattr(u, "role", None) == "SPO")


class LoanRequestViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """
    SPO loan workflow:
    - GET    /loan/meta                    -> metadata for dropdowns
    - GET    /loan/prefill?assessment_id=  -> read-only snapshot for form
    - GET    /loan/eligibility?assessment_id= -> current eligibility + breakdown
    - POST   /loan/                        -> create (submit) loan request
    - GET    /loan/ , /loan/{id}/          -> list/retrieve own requests
    """
    queryset = LoanRequest.objects.select_related("assessment", "organization", "applicant")
    permission_classes = [IsSPO]

    def get_serializer_class(self):
        if self.action == "create":
            return LoanRequestCreateSerializer
        return LoanRequestDetailSerializer

    def get_queryset(self):
        # SPO can only see their org’s requests
        return super().get_queryset().filter(organization__created_by=self.request.user)

    # ---------- A) META ----------
    @extend_schema(
        tags=["Loan • SPO"],
        operation_id="loan_meta",
        summary="Loan Meta (dropdowns, enums, etc.)",
        description="Static metadata required for the **Loan Request Submission** UI.",
        responses={200: LoanMetaSerializer},
        examples=[
            OpenApiExample(
                "Meta example",
                value={"fund_types": [
                    {"value": "WORKING_CAPITAL", "label": "Working capital"},
                    {"value": "GROWTH_CAPITAL",  "label": "Growth capital"},
                    {"value": "EQUIPMENT",       "label": "Equipment/Asset purchase"},
                    {"value": "BRIDGE",          "label": "Bridge financing"},
                    {"value": "OTHER",           "label": "Other"},
                ]}
            )
        ]
    )
    @action(detail=False, methods=["get"], url_path="meta")
    def meta(self, request, *args, **kwargs):
        try:
            return Response({"fund_types": fund_types_meta()}, status=200)
        except Exception as e:
            return Response(
                {"message": "We could not fetch the loan meta right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ---------- B) PREFILL ----------
    @extend_schema(
        tags=["Loan • SPO"],
        operation_id="loan_prefill",
        summary="Prefill data for Loan Request",
        description=(
            "Read-only organization & assessment snapshot (Figma form). "
            "SPO must own the organization for the given `assessment_id`."
        ),
        parameters=[
            OpenApiParameter(
                name="assessment_id",
                type=int, required=True, location=OpenApiParameter.QUERY,
                description="Assessment ID to prefill from"
            )
        ],
        responses={200: LoanPrefillSerializer, 400: None, 403: None, 404: None},
        examples=[
            OpenApiExample(
                "Prefill example",
                value={
                    "assessment_id": 42,
                    "organization": {
                        "name": "xyzorganization Name Private Limited",
                        "date_of_incorporation": "2024-10-02",
                        "dpiit_number": "DPIIT2022AB1234",
                        "legal_registration_type": "PRIVATE_LTD",
                        "cin_number": "U12345MH2022PTC123456",
                        "poc_email": "meenakshi@xyzorganization.com",
                        "focus_area": "Health",
                        "company_type": "Product based",
                        "annual_operating_budget": "100000000.00",
                        "geo_scope": "Pan-India"
                    },
                    "assessment_scores": {"sections": {"IMPACT": 90, "RISK": 20, "RETURN": 90}, "overall": 0}
                }
            )
        ]
    )
    @action(detail=False, methods=["get"], url_path="prefill")
    def prefill(self, request, *args, **kwargs):
        try:
            aid = request.query_params.get("assessment_id")
            if not aid:
                return Response({"message": "assessment_id is required", "errors": {}}, status=400)

            try:
                a = Assessment.objects.select_related("organization").get(id=aid)
            except Assessment.DoesNotExist:
                return Response({"message": "Assessment not found", "errors": {}}, status=404)

            # ownership check
            if a.organization.created_by_id != request.user.id and a.organization not in request.user.organizations.all():
                return Response({"message": "Not allowed", "errors": {}}, status=403)

            org: Organization = a.organization
            org_snapshot = {
                "name": org.name,
                "date_of_incorporation": getattr(org, "incorporation_date", None),
                "dpiit_number": getattr(org, "dpiit_number", None),
                "legal_registration_type": getattr(org, "registration_type", None),
                "cin_number": getattr(org, "cin", None),
                "poc_email": getattr(org, "poc_email", None),
                "focus_area": getattr(org, "focus_area", None),
                "company_type": getattr(org, "company_type", None),
                "annual_operating_budget": getattr(org, "annual_budget", None),
                "geo_scope": getattr(org, "geo_scope", None),
            }
            return Response({
                "assessment_id": a.id,
                "organization": org_snapshot,
                "assessment_scores": a.scores or {},
            }, status=200)
        except Exception as e:
            return Response(
                {"message": "We could not fetch the loan prefill data right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ---------- C) ELIGIBILITY ----------
    @extend_schema(
        tags=["Loan • SPO"],
        operation_id="eligibility_check",
        summary="Check loan eligibility for an Assessment",
        description=(
            "Returns the **current eligibility decision** and detailed breakdown for `assessment_id`. "
            "Call **after** submitting the assessment (scores available)."
        ),
        parameters=[
            OpenApiParameter(
                name="assessment_id",
                type=int, required=True, location=OpenApiParameter.QUERY,
                description="Assessment ID to evaluate"
            )
        ],
        responses={
            200: inline_serializer(
                name="EligibilityCheckResponse",
                fields={
                    "assessment_id": serializers.IntegerField(),
                    "is_eligible": serializers.BooleanField(),
                    "overall_score": serializers.FloatField(),
                    "details": serializers.JSONField()
                }
            ),
            400: inline_serializer(name="BadRequest", fields={"detail": serializers.CharField()}),
            403: inline_serializer(name="Forbidden", fields={"detail": serializers.CharField()}),
            404: inline_serializer(name="NotFound", fields={"detail": serializers.CharField()}),
        },
        examples=[
            OpenApiExample(
                "Eligible example",
                value={
                    "assessment_id": 42,
                    "is_eligible": True,
                    "overall_score": 78.0,
                    "details": {
                        "weights_sum": 100.0,
                        "sections": {
                            "IMPACT": {"normalized": 90, "min": 60, "max": 100, "weight": 40, "gate_pass": True, "contribution": 36.0},
                            "RISK":   {"normalized": 20, "min": 0,  "max":  40, "weight": 30, "gate_pass": True, "contribution": 6.0},
                            "RETURN": {"normalized": 90, "min": 50, "max": 100, "weight": 30, "gate_pass": True, "contribution": 27.0}
                        }
                    }
                }
            )
        ]
    )
    @action(detail=False, methods=["get"], url_path="eligibility")
    def eligibility(self, request, *args, **kwargs):
        try:
            aid = request.query_params.get("assessment_id")
            if not aid:
                return Response({"message": "assessment_id is required", "errors": {}}, status=400)

            try:
                a = Assessment.objects.select_related("organization").get(id=aid)
            except Assessment.DoesNotExist:
                return Response({"message": "Assessment not found", "errors": {}}, status=404)

            if a.organization.created_by_id != request.user.id and a.organization not in request.user.organizations.all():
                return Response({"message": "Not allowed", "errors": {}}, status=403)

            res = eligibility_check(a)
            return Response({
                "assessment_id": a.id,
                "is_eligible": res.is_eligible,
                "overall_score": float(res.overall_score),
                "details": res.details,
            }, status=200)
        except Exception as e:
            return Response(
                {"message": "We could not check the loan eligibility right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ---------- D) CREATE (SUBMIT) ----------
    @extend_schema(
        tags=["Loan • SPO"],
        operation_id="loan_request_create",
        summary="Submit Loan Request",
        description=(
            "Creates a **LoanRequest** for the given `assessment` **only if** it is currently eligible. "
            "Server re-runs eligibility at submission time."
        ),
        request=LoanRequestCreateSerializer,
        responses={
            201: LoanRequestDetailSerializer,
            400: inline_serializer(name="BadRequest", fields={"detail": serializers.CharField()}),
            403: inline_serializer(name="Forbidden", fields={"detail": serializers.CharField()}),
        },
        examples=[
            OpenApiExample(
                "Submit request (body)",
                request_only=True,
                value={
                    "assessment": 42,
                    "founder_name": "A Founder",
                    "founder_email": "founder@xyz.com",
                    "amount_in_inr": "5000000.00",
                    "fund_type": "GROWTH_CAPITAL"
                }
            )
        ]
    )
    def create(self, request, *args, **kwargs):
        try:
            ser = LoanRequestCreateSerializer(data=request.data, context={"request": request})
            ser.is_valid(raise_exception=True)

            a: Assessment = ser.validated_data["assessment"]

            # ownership check mirrors read endpoints
            if a.organization.created_by_id != request.user.id and a.organization not in request.user.organizations.all():
                return Response({"message": "Not allowed", "errors": {}}, status=403)

            elig = eligibility_check(a)
            if not elig.is_eligible:
                return Response({"message": "Assessment not eligible for loan.", "errors": {}}, status=400)

            obj: LoanRequest = LoanRequest.objects.create(
                assessment=a,
                organization=a.organization,
                applicant=request.user,
                founder_name=ser.validated_data["founder_name"],
                founder_email=ser.validated_data["founder_email"],
                amount_in_inr=ser.validated_data["amount_in_inr"],
                fund_type=ser.validated_data["fund_type"],
                eligibility_overall=elig.overall_score,
                eligibility_decision=elig.is_eligible,
                eligibility_details=elig.details,
                status=LoanRequest.Status.SUBMITTED,
                submitted_at=timezone.now(),
            )
            return Response(LoanRequestDetailSerializer(obj).data, status=status.HTTP_201_CREATED)
        except Exception as e:
            return Response(
                {"message": "We could not submit the loan request right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )