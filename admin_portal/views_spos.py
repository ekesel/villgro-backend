import logging

from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.contrib.auth import get_user_model
from django.http import Http404, HttpResponse
from rest_framework.exceptions import ValidationError
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiResponse, OpenApiExample
from admin_portal.permissions import IsAdminRole
from admin_portal.serializers import (
    AdminSPOListSerializer, AdminSPOCreateSerializer, AdminSPOUpdateSerializer
)
from django.db.models import Q
from weasyprint import HTML
from django.template.loader import render_to_string
from django.utils.timezone import localtime
from assessments.models import Assessment
from questionnaires.models import LoanEligibilityResult
from django.db.models import Prefetch

User = get_user_model()
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

        status_param = self.request.query_params.get("status")
        if status_param in ("active", "inactive"):
            qs = qs.filter(is_active=(status_param == "active"))

        q = self.request.query_params.get("q")
        if q:
            qs = qs.filter(
                Q(email__icontains=q)
                | Q(first_name__icontains=q)
                | Q(last_name__icontains=q)
                | Q(organization__name__icontains=q)
            )

        ordering = self.request.query_params.get("ordering") or "-date_joined"
        allowed = {"email","-email","first_name","-first_name","date_joined","-date_joined"}
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
        description="List Startup (SPO) users with search, status filter, and ordering.",
        parameters=[
            OpenApiParameter(name="q", description="Search email / name / organization", required=False, type=str),
            OpenApiParameter(name="status", description="Filter by status: active | inactive", required=False, type=str),
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
                    "organization": {"id": 7, "name": "GreenTech Pvt", "registration_type": "PRIVATE_LTD"}
                }]
            )
        ],
    )
    def list(self, *args, **kwargs):
        return super().list(*args, **kwargs)

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
        ser = self.get_serializer(data=request.data)
        try:
            ser.is_valid(raise_exception=True)
        except ValidationError as exc:
            logger.info("Admin SPO create validation failed for %s: %s", request.data.get("email"), exc.detail)
            return Response(
                {"message": "Please fix the highlighted fields.", "errors": exc.detail},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception:
            logger.exception("Unexpected error validating SPO create payload")
            return Response(
                {"message": "We could not create the SPO right now. Please try again later."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        try:
            user = ser.save()
        except Exception:
            logger.exception("Failed to create SPO user for %s", ser.validated_data.get("email"))
            return Response(
                {"message": "We could not create the SPO right now. Please try again later."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(AdminSPOListSerializer(user).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        summary="Retrieve SPO",
        responses={200: AdminSPOListSerializer, 404: OpenApiResponse(description="Not found")},
    )
    def retrieve(self, *args, **kwargs):
        return super().retrieve(*args, **kwargs)

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
                {"message": "Please fix the highlighted fields.", "errors": exc.detail},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Http404:
            logger.info("SPO not found for update: %s", kwargs.get(self.lookup_field))
            return Response({"message": "SPO not found."}, status=status.HTTP_404_NOT_FOUND)
        except Exception:
            logger.exception("Failed to update SPO %s", kwargs.get(self.lookup_field))
            return Response(
                {"message": "We could not update the SPO right now. Please try again later."},
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
                {"message": "Please fix the highlighted fields.", "errors": exc.detail},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Http404:
            logger.info("SPO not found for partial update: %s", kwargs.get(self.lookup_field))
            return Response({"message": "SPO not found."}, status=status.HTTP_404_NOT_FOUND)
        except Exception:
            logger.exception("Failed to partially update SPO %s", kwargs.get(self.lookup_field))
            return Response(
                {"message": "We could not update the SPO right now. Please try again later."},
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
            return Response({"message": "SPO not found."}, status=status.HTTP_404_NOT_FOUND)
        except Exception:
            logger.exception("Failed to fetch SPO for delete %s", kwargs.get(self.lookup_field))
            return Response(
                {"message": "We could not fetch the SPO right now. Please try again later."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        try:
            if hasattr(user, "organization"):
                user.organization.delete()
            user.delete()
        except Exception:
            logger.exception("Failed to delete SPO %s", user.pk)
            return Response(
                {"message": "We could not delete the SPO right now. Please try again later."},
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
            user = self.get_object()
        except Http404:
            logger.info("SPO not found for toggle: %s", pk)
            return Response({"message": "SPO not found."}, status=status.HTTP_404_NOT_FOUND)
        except Exception:
            logger.exception("Failed to fetch SPO for toggle %s", pk)
            return Response(
                {"message": "We could not update the SPO right now. Please try again later."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        try:
            user.is_active = not user.is_active
            user.save(update_fields=["is_active"])
        except Exception:
            logger.exception("Failed to toggle status for SPO %s", user.pk)
            return Response(
                {"message": "We could not update the SPO right now. Please try again later."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(AdminSPOListSerializer(user).data)
    
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
            return Response({"message": "SPO not found."}, status=status.HTTP_404_NOT_FOUND)
        except Exception:
            logger.exception("Failed to fetch SPO for report %s", pk)
            return Response(
                {"message": "We could not generate the report right now. Please try again later."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        org = getattr(spo, "organization", None)
        if not org:
            logger.info("Organization missing for SPO %s during report generation", spo.pk)
            return Response({"detail": "Organization not found for this SPO"}, status=status.HTTP_404_NOT_FOUND)

        try:
            assessments = Assessment.objects.filter(organization=org).order_by("-submitted_at", "-started_at")
            elig_map = {e.assessment_id: e for e in LoanEligibilityResult.objects.filter(assessment__in=assessments)}
        except Exception:
            logger.exception("Failed to collect assessment data for SPO %s", spo.pk)
            return Response(
                {"message": "We could not generate the report right now. Please try again later."},
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
        except Exception:
            logger.exception("Failed to render PDF report for SPO %s", spo.pk)
            return Response(
                {"message": "We could not generate the report right now. Please try again later."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        resp = HttpResponse(pdf_bytes, content_type="application/pdf")
        resp["Content-Disposition"] = f'attachment; filename="spo-report-{spo.id}.pdf"'
        return resp
