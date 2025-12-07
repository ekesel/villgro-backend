import logging

from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from accounts.models import User
from django.http import Http404, HttpResponse
from rest_framework.exceptions import ValidationError
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiResponse, OpenApiExample
from admin_portal.permissions import IsAdminRole
from admin_portal.serializers import (
    AdminSPOListSerializer, AdminSPOCreateSerializer, AdminSPOUpdateSerializer
)
from django.utils.dateparse import parse_date
from django.db.models import Q
from weasyprint import HTML
from django.template.loader import render_to_string
from django.utils.timezone import localtime
from assessments.models import Assessment
from questionnaires.models import LoanEligibilityResult
from django.shortcuts import get_object_or_404
from questionnaires.models import LoanEligibilityResult, Section, Question
from assessments.services import build_answers_map
from questionnaires.utils import _build_validation_message
logger = logging.getLogger(__name__)

@extend_schema(tags=["Admin • SPOs"])
class SPOAdminViewSet(viewsets.ModelViewSet):
    """
    Manage SPO users (role=SPO) and their Organization (inline).
    """
    permission_classes = [IsAdminRole]
    lookup_field = "pk"

    def get_queryset(self):
        qs = (
            User.objects.filter(role=User.Role.SPO)
            .select_related("organization")
        )

        # Status filter
        status_param = self.request.query_params.get("status")
        if status_param in ("active", "inactive"):
            qs = qs.filter(is_active=(status_param == "active"))

        # Search filter
        q = self.request.query_params.get("q")
        if q:
            qs = qs.filter(
                Q(email__icontains=q)
                | Q(first_name__icontains=q)
                | Q(last_name__icontains=q)
                | Q(organization__name__icontains=q)
            )

        # Date range filter (by date_joined DATE)
        start_date_str = self.request.query_params.get("start_date")
        end_date_str = self.request.query_params.get("end_date")
        start_date = parse_date(start_date_str) if start_date_str else None
        end_date = parse_date(end_date_str) if end_date_str else None

        if start_date:
            qs = qs.filter(date_joined__date__gte=start_date)
        if end_date:
            qs = qs.filter(date_joined__date__lte=end_date)

        # Ordering
        ordering = self.request.query_params.get("ordering") or "-date_joined"
        allowed = {"email", "-email", "first_name", "-first_name", "date_joined", "-date_joined"}
        qs = qs.order_by(ordering if ordering in allowed else "-date_joined")
        return qs

    def get_serializer_class(self):
        if self.action == "create":
            return AdminSPOCreateSerializer
        if self.action in ("update", "partial_update"):
            return AdminSPOUpdateSerializer
        return AdminSPOListSerializer

    @extend_schema(
        summary="List SPOs",
        description=(
            "List Startup (SPO) users with search, status filter, date range, and ordering.\n\n"
            "Each row is enriched with:\n"
            "- `loan_eligible`: true if ANY eligible LoanEligibilityResult exists for the SPO's organization\n"
            "- `instrument`: the latest eligible matched instrument (id + name), if present\n"
            "- `scores`: summary of the latest eligible assessment (overall + IMPACT/RISK/RETURN section scores)"
        ),
        parameters=[
            OpenApiParameter(name="q", description="Search email / name / organization", required=False, type=str),
            OpenApiParameter(name="status", description="Filter by status: active | inactive", required=False, type=str),
            OpenApiParameter(
                name="start_date",
                description="Filter SPOs by joined date (YYYY-MM-DD, inclusive start)",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="end_date",
                description="Filter SPOs by joined date (YYYY-MM-DD, inclusive end)",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="ordering",
                description="Sort by: email, -email, first_name, -first_name, date_joined, -date_joined",
                required=False, type=str
            ),
        ],
        responses={200: AdminSPOListSerializer(many=True)},
        examples=[
            OpenApiExample(
                "List response (truncated)",
                value=[{
                    "id": 12,
                    "email": "spo@startup.com",
                    "first_name": "Asha",
                    "last_name": "Verma",
                    "phone": "9876543210",
                    "is_active": True,
                    "date_joined": "2025-10-01T10:15:00Z",
                    "loan_eligible": True,
                    "instrument": {
                        "id": 4,
                        "name": "Commercial debt with impact linked incentives"
                    },
                    "scores": {
                        "overall": 78.5,
                        "sections": {
                            "IMPACT": 82.0,
                            "RISK": 25.0,
                            "RETURN": 75.0
                        }
                    },
                    "organization": {
                        "id": 7,
                        "name": "GreenTech Pvt",
                        "registration_type": "PRIVATE_LTD"
                    }
                }]
            )
        ],
    )
    def list(self, request, *args, **kwargs):
        """
        Enrich paginated list with:
        - 'loan_eligible': True if ANY LoanEligibilityResult for this SPO's organization is eligible.
        - 'instrument': latest eligible matched instrument (if any) for this SPO.
        - 'scores': summary (overall + IMPACT/RISK/RETURN) of the latest eligible assessment.
        """
        try:
            response = super().list(request, *args, **kwargs)

            # response.data can be paginated dict with 'results' or a plain list
            if isinstance(response.data, dict) and "results" in response.data:
                items = response.data["results"]
            else:
                items = response.data

            # Collect SPO user ids present on the page
            user_ids = [it.get("id") for it in items if isinstance(it, dict) and it.get("id")]

            elig_map = {}
            inst_map = {}
            scores_map = {}
            latest_assessment_map = {}

            if user_ids:
                # Fetch all ELIGIBLE results for these SPOs, newest first
                elig_qs = (
                    LoanEligibilityResult.objects
                    .select_related("matched_instrument", "assessment__organization__created_by")
                    .filter(assessment__organization__created_by_id__in=user_ids, is_eligible=True)
                    .order_by(
                        "-evaluated_at",
                        "-assessment__submitted_at",
                        "-assessment__started_at",
                    )
                )

                for elig in elig_qs:
                    org = getattr(elig.assessment, "organization", None)
                    if not org:
                        continue
                    spo_id = org.created_by_id
                    if spo_id in elig_map:
                        continue  # already have latest one

                    elig_map[spo_id] = True

                    latest_assessment_map[spo_id] = elig.assessment_id

                    # Instrument payload
                    inst = elig.matched_instrument
                    if inst:
                        inst_map[spo_id] = {
                            "id": inst.id,
                            "name": inst.name,
                        }
                    else:
                        inst_map[spo_id] = None

                    # Scores payload (from eligibility details)
                    sec_details = (elig.details or {}).get("sections", {}) if elig.details else {}
                    impact_norm = (sec_details.get("IMPACT") or {}).get("normalized")
                    risk_norm = (sec_details.get("RISK") or {}).get("normalized")
                    return_norm = (sec_details.get("RETURN") or {}).get("normalized")

                    scores_map[spo_id] = {
                        "overall": float(elig.overall_score) if elig.overall_score is not None else None,
                        "sections": {
                            "IMPACT": impact_norm,
                            "RISK": risk_norm,
                            "RETURN": return_norm,
                        },
                    }

            # Inject the flags per row (defaults)
            for it in items:
                uid = it.get("id")
                it["loan_eligible"] = bool(elig_map.get(uid, False))
                it["instrument"] = inst_map.get(uid, None)
                it["scores"] = scores_map.get(uid, None)
                it["assessment_id"] = latest_assessment_map.get(uid, None)

            return response
        except Exception as e:
            logger.exception("Failed to list SPO users")
            return Response(
                {
                    "message": "We could not fetch the SPO users right now. Please try again later.",
                    "errors": str(e),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        summary="Create SPO (user + organization)",
        description="Creates a new SPO user and an associated Organization record.",
        request=AdminSPOCreateSerializer,
        responses={
            201: AdminSPOListSerializer,
            400: OpenApiResponse(description="Validation error"),
        },
        examples=[
            OpenApiExample(
                "Create request",
                value={
                    "email": "newspo@example.com",
                    "first_name": "Neha",
                    "last_name": "Singh",
                    "phone": "9999999999",
                    "password": "StrongPass123!",
                    "organization": {
                        "name": "Acme Climate",
                        "registration_type": "PRIVATE_LTD"
                    }
                }
            )
        ],
    )
    def create(self, request, *args, **kwargs):
        try:
            ser = self.get_serializer(data=request.data)
            try:
                ser.is_valid(raise_exception=True)
            except ValidationError as exc:
                logger.info("Admin SPO create validation failed for %s: %s", request.data.get("email"), exc.detail)
                return Response(
                    {"message":  _build_validation_message(exc.detail), "errors": exc.detail},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            except Exception as e:
                logger.exception("Unexpected error validating SPO create payload")
                return Response(
                    {"message": "We could not create the SPO right now. Please try again later.", "errors": str(e)},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
            try:
                user = ser.save()
            except Exception as e:
                logger.exception("Failed to create SPO user for %s", ser.validated_data.get("email"))
                return Response(
                    {"message": "We could not create the SPO right now. Please try again later.", "errors": str(e)},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
            return Response(AdminSPOListSerializer(user).data, status=status.HTTP_201_CREATED)
        except Exception as e:
            logger.exception("Failed to create SPO user")
            return Response(
                {"message": "We could not create the SPO right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        summary="Retrieve SPO (enriched, same shape as list row)",
        description=(
            "Retrieve a single Startup (SPO) user by id.\n\n"
            "The response shape matches a single row from the list API and is enriched with:\n"
            "- `loan_eligible`: true if ANY eligible LoanEligibilityResult exists for the SPO's organization\n"
            "- `instrument`: the latest eligible matched instrument (id + name), if present\n"
            "- `scores`: summary of the latest eligible assessment (overall + IMPACT/RISK/RETURN section scores)\n"
            "- `assessment_id`: id of the latest eligible assessment (if any)"
        ),
        responses={
            200: AdminSPOListSerializer,
            404: OpenApiResponse(description="Not found"),
        },
        examples=[
            OpenApiExample(
                "Retrieve response (enriched)",
                value={
                    "id": 12,
                    "email": "spo@startup.com",
                    "first_name": "Asha",
                    "last_name": "Verma",
                    "phone": "9876543210",
                    "is_active": True,
                    "date_joined": "2025-10-01T10:15:00Z",
                    "loan_eligible": True,
                    "assessment_id": 123,
                    "instrument": {
                        "id": 4,
                        "name": "Commercial debt with impact linked incentives"
                    },
                    "scores": {
                        "overall": 78.5,
                        "sections": {
                            "IMPACT": 82.0,
                            "RISK": 25.0,
                            "RETURN": 75.0
                        }
                    },
                    "organization": {
                        "id": 7,
                        "name": "GreenTech Pvt",
                        "registration_type": "PRIVATE_LTD"
                    }
                },
                response_only=True,
            )
        ],
    )
    def retrieve(self, request, *args, **kwargs):
        """
        Retrieve a single SPO in the same enriched shape
        as a row from the list endpoint.
        """
        try:
            try:
                user = self.get_object()
            except Http404:
                logger.info("SPO not found for retrieve: %s", kwargs.get(self.lookup_field))
                return Response(
                    {"message": "SPO not found.", "errors": {}},
                    status=status.HTTP_404_NOT_FOUND,
                )
            except Exception as e:
                logger.exception("Failed to fetch SPO for retrieve %s", kwargs.get(self.lookup_field))
                return Response(
                    {
                        "message": "We could not fetch the SPO right now. Please try again later.",
                        "errors": str(e),
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            # Base payload from serializer (same as list)
            data = AdminSPOListSerializer(user).data

            spo_id = user.id
            loan_eligible = False
            instrument_payload = None
            scores_payload = None
            latest_assessment_id = None

            try:
                # Latest ELIGIBLE result for this SPO (if any)
                elig_qs = (
                    LoanEligibilityResult.objects
                    .select_related("matched_instrument", "assessment__organization__created_by")
                    .filter(assessment__organization__created_by_id=spo_id, is_eligible=True)
                    .order_by(
                        "-evaluated_at",
                        "-assessment__submitted_at",
                        "-assessment__started_at",
                    )
                )
                elig = elig_qs.first()

                if elig is not None:
                    loan_eligible = True
                    latest_assessment_id = elig.assessment_id

                    inst = elig.matched_instrument
                    if inst:
                        instrument_payload = {
                            "id": inst.id,
                            "name": inst.name,
                        }

                    sec_details = (elig.details or {}).get("sections", {}) if elig.details else {}
                    impact_norm = (sec_details.get("IMPACT") or {}).get("normalized")
                    risk_norm = (sec_details.get("RISK") or {}).get("normalized")
                    return_norm = (sec_details.get("RETURN") or {}).get("normalized")

                    scores_payload = {
                        "overall": float(elig.overall_score) if elig.overall_score is not None else None,
                        "sections": {
                            "IMPACT": impact_norm,
                            "RISK": risk_norm,
                            "RETURN": return_norm,
                        },
                    }
            except Exception as e:
                logger.exception("Failed to enrich SPO retrieve with eligibility data for %s", spo_id)
                # we still return base data, just without enrichment
                # but we keep error structure consistent
                return Response(
                    {
                        "message": "We could not fetch eligibility data for this SPO.",
                        "errors": str(e),
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            # Inject enrichment fields (same as list)
            data["loan_eligible"] = loan_eligible
            data["instrument"] = instrument_payload
            data["scores"] = scores_payload
            data["assessment_id"] = latest_assessment_id

            return Response(data, status=status.HTTP_200_OK)

        except Exception as e:
            logger.exception("Failed to retrieve SPO %s", kwargs.get(self.lookup_field))
            return Response(
                {
                    "message": "We could not fetch the SPO right now. Please try again later.",
                    "errors": str(e),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        summary="Update SPO & Organization (PUT)",
        request=AdminSPOUpdateSerializer,
        responses={200: AdminSPOListSerializer, 400: OpenApiResponse(description="Validation error")},
    )
    def update(self, request, *args, **kwargs):
        try:
            return super().update(request, *args, **kwargs)
        except ValidationError as exc:
            logger.info("Admin SPO update validation failed for %s: %s", kwargs.get(self.lookup_field), exc.detail)
            return Response(
                {"message":  _build_validation_message(exc.detail), "errors": exc.detail},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Http404:
            logger.info("SPO not found for update: %s", kwargs.get(self.lookup_field))
            return Response({"message": "SPO not found.", "errors": {}}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.exception("Failed to update SPO %s", kwargs.get(self.lookup_field))
            return Response(
                {"message": "We could not update the SPO right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        summary="Partial update SPO & Organization (PATCH)",
        request=AdminSPOUpdateSerializer,
        responses={200: AdminSPOListSerializer},
    )
    def partial_update(self, request, *args, **kwargs):
        try:
            return super().partial_update(request, *args, **kwargs)
        except ValidationError as exc:
            logger.info("Admin SPO partial update validation failed for %s: %s", kwargs.get(self.lookup_field), exc.detail)
            return Response(
                {"message":  _build_validation_message(exc.detail), "errors": exc.detail},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Http404:
            logger.info("SPO not found for partial update: %s", kwargs.get(self.lookup_field))
            return Response({"message": "SPO not found.", "errors": {}}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.exception("Failed to partially update SPO %s", kwargs.get(self.lookup_field))
            return Response(
                {"message": "We could not update the SPO right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        summary="Delete SPO (and its Organization)",
        responses={204: OpenApiResponse(description="Deleted"), 404: OpenApiResponse(description="Not found")},
    )
    def destroy(self, request, *args, **kwargs):
        try:
            user = self.get_object()
        except Http404:
            logger.info("SPO not found for delete: %s", kwargs.get(self.lookup_field))
            return Response({"message": "SPO not found.", "errors": {}}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.exception("Failed to fetch SPO for delete %s", kwargs.get(self.lookup_field))
            return Response(
                {"message": "We could not fetch the SPO right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        try:
            if hasattr(user, "organization"):
                user.organization.delete()
            user.delete()
        except Exception as e:
            logger.exception("Failed to delete SPO %s", user.pk)
            return Response(
                {"message": "We could not delete the SPO right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["patch"], url_path="toggle-status")
    @extend_schema(
        summary="Enable/disable SPO",
        description="Flips `is_active` for the SPO.",
        responses={200: AdminSPOListSerializer},
        examples=[OpenApiExample("Response", value={"id": 12, "is_active": False})],
    )
    def toggle_status(self, request, pk=None):
        try:
            try:
                user = self.get_object()
            except Http404:
                logger.info("SPO not found for toggle: %s", pk)
                return Response({"message": "SPO not found.", "errors": {}}, status=status.HTTP_404_NOT_FOUND)
            except Exception as e:
                logger.exception("Failed to fetch SPO for toggle %s", pk)
                return Response(
                    {"message": "We could not update the SPO right now. Please try again later.", "errors": str(e)},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
            try:
                user.is_active = not user.is_active
                user.save(update_fields=["is_active"])
            except Exception as e:
                logger.exception("Failed to toggle status for SPO %s", user.pk)
                return Response(
                    {"message": "We could not update the SPO right now. Please try again later.", "errors": str(e)},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
            return Response(AdminSPOListSerializer(user).data)
        except Exception as e:
            logger.exception("Failed to toggle status for SPO %s", pk)
            return Response(
                {"message": "We could not update the SPO right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=True, methods=["get"], url_path="report")
    @extend_schema(
        summary="Download SPO Report (PDF)",
        description=(
            "Admin-only PDF report for a specific SPO user, including:\n"
            "- SPO and Organization details\n"
            "- All Assessments (status, timestamps, section & overall scores)\n"
            "- Loan eligibility results (score, decision, and notes)"
        ),
        responses={
            200: OpenApiResponse(description="PDF binary"),
            404: OpenApiResponse(description="SPO or Organization not found"),
        },
    )
    def report(self, request, pk=None):
        try:
            spo = self.get_object()
        except Http404:
            logger.info("SPO not found for report: %s", pk)
            return Response({"message": "SPO not found.", "errors": {}}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.exception("Failed to fetch SPO for report %s", pk)
            return Response(
                {"message": "We could not generate the report right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        org = getattr(spo, "organization", None)
        if not org:
            logger.info("Organization missing for SPO %s during report generation", spo.pk)
            return Response({"message": "Organization not found for this SPO", "errors": {}}, status=status.HTTP_404_NOT_FOUND)

        try:
            assessments = Assessment.objects.filter(organization=org).order_by("-submitted_at", "-started_at")
            elig_map = {e.assessment_id: e for e in LoanEligibilityResult.objects.filter(assessment__in=assessments)}
        except Exception as e:
            logger.exception("Failed to collect assessment data for SPO %s", spo.pk)
            return Response(
                {"message": "We could not generate the report right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        try:
            html = render_to_string(
                "admin_portal/spo_report.html",
                {
                    "spo": spo,
                    "org": org,
                    "assessments": assessments,
                    "elig_map": elig_map,
                    "generated_at": localtime().strftime("%Y-%m-%d %H:%M"),
                },
            )
            pdf_bytes = HTML(string=html).write_pdf()
        except Exception as e:
            logger.exception("Failed to render PDF report for SPO %s", spo.pk)
            return Response(
                {"message": "We could not generate the report right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        resp = HttpResponse(pdf_bytes, content_type="application/pdf")
        resp["Content-Disposition"] = f'attachment; filename="spo-report-{spo.id}.pdf"'
        return resp

    @action(detail=True, methods=["get"], url_path="assessments")
    @extend_schema(
        summary="List assessments for a specific SPO (Admin)",
        description=(
            "Returns all assessments belonging to the SPO's Organization. "
            "Includes status, timestamps, section/overall scores, matched loan instrument (from eligibility), "
            "and eligibility decision."
        ),
        responses={
            200: OpenApiResponse(description="List of assessments for the SPO’s organization"),
            404: OpenApiResponse(description="SPO or Organization not found"),
        },
        examples=[
            OpenApiExample(
                "Assessments list (truncated)",
                value=[
                    {
                        "id": 123,
                        "status": "SUBMITTED",
                        "started_at": "2025-09-24T10:30:00Z",
                        "submitted_at": "2025-09-24T11:05:00Z",
                        "scores": {
                            "overall": 76.5,
                            "sections": {"IMPACT": 79.0, "RISK": 72.0, "RETURN": 78.0}
                        },
                        "instrument": {"id": 4, "name": "Commercial Debt with Impact…"},
                        "eligibility": {
                            "is_eligible": True,
                            "overall_score": 72.0,
                            "reason": None
                        }
                    }
                ],
                response_only=True,
            )
        ],
    )
    def assessments(self, request, pk=None):
        """
        Drives the Admin • SPO Detail > Assessment Summary table.
        GET /api/admin/spos/{spo_id}/assessments/
        """
        try:
            spo = self.get_object()
        except Http404:
            logger.info("SPO not found for assessments: %s", pk)
            return Response({"message": "SPO not found.", "errors": {}}, status=404)
        except Exception as e:
            logger.exception("Failed to fetch SPO for assessments %s", pk)
            return Response({"message": "Unable to fetch SPO.", "errors": str(e)}, status=500)

        org = getattr(spo, "organization", None)
        if not org:
            logger.info("Organization not found for SPO %s when listing assessments", pk)
            return Response({"message": "Organization not found.", "errors": {}}, status=404)

        try:
            # Pull assessments for org
            qs = (
                Assessment.objects
                .filter(organization=org)
                .order_by("-submitted_at", "-started_at")
            )

            # Eligibility map (with instrument on the eligibility)
            elig_qs = (
                LoanEligibilityResult.objects
                .select_related("matched_instrument")
                .filter(assessment__in=qs)
            )
            elig_map = {e.assessment_id: e for e in elig_qs}

            data = []
            for a in qs:
                s = a.scores or {}
                sections = (s.get("sections") or {})
                elig = elig_map.get(a.id)
                inst = getattr(elig, "matched_instrument", None) if elig else None

                data.append({
                    "id": a.id,
                    "status": a.status,
                    "started_at": a.started_at,
                    "submitted_at": a.submitted_at,
                    "scores": {
                        "overall": s.get("overall", 0),
                        "sections": {
                            "IMPACT": sections.get("IMPACT", 0),
                            "RISK": sections.get("RISK", 0),
                            "RETURN": sections.get("RETURN", 0),
                        },
                    },
                    "instrument": (
                        {"id": getattr(inst, "id", None), "name": getattr(inst, "name", None)}
                        if inst else None
                    ),
                    "eligibility": (
                        {
                            "is_eligible": bool(getattr(elig, "is_eligible", False)),
                            "overall_score": getattr(elig, "overall_score", None),
                            "reason": (getattr(elig, "details", {}) or {}).get("reason")
                                      if getattr(elig, "details", None) else None,
                        }
                        if elig else None
                    ),
                })
        except Exception as e:
            logger.exception("Failed to build assessment list for SPO %s", pk)
            return Response({"message": "Unable to list assessments.", "errors": str(e)}, status=500)

        return Response(data, status=200)
    
    @action(
        detail=True,
        methods=["get"],
        url_path=r"assessments/(?P<assessment_id>\d+)/qa",
    )
    @extend_schema(
        summary="View questions and answers for a specific assessment (Admin)",
        description=(
            "For a given SPO (path param) and assessment id (in URL), "
            "returns all sections, questions and the SPO's answers for that assessment.\n\n"
            "The assessment is constrained to the SPO's organization."
        ),
        parameters=[
            OpenApiParameter(
                name="assessment_id",
                required=True,
                type=int,
                description="ID of the assessment belonging to this SPO's organization"
            )
        ],
        responses={
            200: OpenApiResponse(
                description="Questions and answers for the assessment",
                examples=[
                    OpenApiExample(
                        "QA payload (truncated)",
                        value={
                            "assessment_id": 123,
                            "status": "SUBMITTED",
                            "started_at": "2025-09-24T10:30:00Z",
                            "submitted_at": "2025-09-24T11:05:00Z",
                            "sections": [
                                {
                                    "code": "IMPACT",
                                    "name": "Impact",
                                    "questions": [
                                        {
                                            "code": "Q_1763437992691",
                                            "text": "What is the impact on access to the product/service for the target group?",
                                            "type": "SINGLE_CHOICE",
                                            "answer": {
                                                "value": "HIGH"
                                            }
                                        }
                                    ]
                                }
                            ]
                        },
                        response_only=True,
                    )
                ],
            ),
            404: OpenApiResponse(description="SPO, Organization, or Assessment not found"),
        },
    )
    def assessment_qa(self, request, pk=None, assessment_id=None):
        """
        GET /api/admin/spos/{spo_id}/assessments/{assessment_id}/qa/

        Returns structured list of sections -> questions -> answers
        for the specified assessment, scoped to this SPO's organization.
        """
        try:
            try:
                spo = self.get_object()
            except Http404:
                logger.info("SPO not found for assessment QA: spo_id=%s", pk)
                return Response(
                    {"message": "SPO not found.", "errors": {}},
                    status=status.HTTP_404_NOT_FOUND,
                )
            except Exception as e:
                logger.exception("Failed to fetch SPO for assessment QA: spo_id=%s", pk)
                return Response(
                    {
                        "message": "We could not fetch the SPO right now. Please try again later.",
                        "errors": str(e),
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            org = getattr(spo, "organization", None)
            if not org:
                logger.info("Organization not found for SPO %s when fetching assessment QA", pk)
                return Response(
                    {"message": "Organization not found.", "errors": {}},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Ensure the assessment belongs to this SPO's organization
            try:
                assessment = get_object_or_404(
                    Assessment,
                    id=assessment_id,
                    organization=org,
                )
            except Http404:
                logger.info(
                    "Assessment %s not found for SPO %s (org=%s)",
                    assessment_id, pk, org.id
                )
                return Response(
                    {"message": "Assessment not found for this SPO.", "errors": {}},
                    status=status.HTTP_404_NOT_FOUND,
                )
            except Exception as e:
                logger.exception(
                    "Failed to fetch Assessment %s for SPO %s",
                    assessment_id, pk
                )
                return Response(
                    {
                        "message": "We could not fetch the assessment right now. Please try again later.",
                        "errors": str(e),
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            # Build answer map using existing logic helper
            try:
                answers_map = build_answers_map(assessment)  # { "Q_CODE": {...raw answer...}, ... }
            except Exception as e:
                logger.exception(
                    "Failed to build answers map for Assessment %s (SPO %s)",
                    assessment_id, pk
                )
                return Response(
                    {
                        "message": "We could not load answers for this assessment.",
                        "errors": str(e),
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            # Build sections -> questions -> answers payload
            sections_payload = []
            try:
                sections = Section.objects.all().order_by("order")
                for sec in sections:
                    # Only include questions from this section
                    questions = (
                        Question.objects
                        .filter(section=sec, is_active=True, sector=org.focus_sector)
                        .order_by("order")
                    )

                    q_payload = []
                    for q in questions:
                        q_payload.append({
                            "code": q.code,
                            "text": q.text,
                            "type": q.type,
                            "answer": answers_map.get(q.code),  # raw stored answer dict or None
                        })

                    if q_payload:
                        sections_payload.append({
                            "code": sec.code,
                            "name": sec.title,
                            "questions": q_payload,
                        })
            except Exception as e:
                logger.exception(
                    "Failed to build QA payload for Assessment %s (SPO %s)",
                    assessment_id, pk
                )
                return Response(
                    {
                        "message": "We could not build the questions and answers view.",
                        "errors": str(e),
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            out = {
                "assessment_id": assessment.id,
                "status": assessment.status,
                "started_at": assessment.started_at,
                "submitted_at": assessment.submitted_at,
                "sections": sections_payload,
            }
            return Response(out, status=status.HTTP_200_OK)

        except Exception as e:
            logger.exception(
                "Unexpected error in assessment_qa for SPO %s, assessment %s",
                pk, assessment_id
            )
            return Response(
                {
                    "message": "We could not fetch the assessment questions right now. Please try again later.",
                    "errors": str(e),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )