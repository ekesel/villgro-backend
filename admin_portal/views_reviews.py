# admin_portal/views_reviews.py
from rest_framework import viewsets
from rest_framework.response import Response
from django.db.models import Q
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiResponse, OpenApiExample
from admin_portal.permissions import IsAdminRole
from assessments.models import AssessmentFeedback
from admin_portal.serializers import AdminReviewListSerializer, AdminReviewDetailSerializer

@extend_schema(tags=["Admin • Reviews"])
class AdminReviewsViewSet(viewsets.ViewSet):
    permission_classes = [IsAdminRole]

    @extend_schema(
        summary="List SPO feedback (Admin)",
        description=(
            "Lists all feedback entries across SPOs. "
            "Filters: status=completed|incomplete, q (user email/name/org or review text). "
            "Sort: ordering = id|-id|date|-date. Paginated 50."
        ),
        parameters=[
            OpenApiParameter(name="status", required=False, type=str, description="completed | incomplete"),
            OpenApiParameter(name="q", required=False, type=str, description="search user/org/review text"),
            OpenApiParameter(name="ordering", required=False, type=str, description="id|-id|date|-date"),
            OpenApiParameter(name="page", required=False, type=int),
        ],
        responses={200: AdminReviewListSerializer(many=True)},
    )
    def list(self, request):
        qs = AssessmentFeedback.objects.select_related(
            "assessment", "assessment__organization", "assessment__organization__created_by"
        )

        # filters
        status_param = (request.query_params.get("status") or "").lower()
        if status_param in ("completed", "incomplete"):
            if status_param == "completed":
                qs = qs.filter(assessment__status="SUBMITTED")
            else:
                qs = qs.exclude(assessment__status="SUBMITTED")

        q = request.query_params.get("q")
        if q:
            qs = qs.filter(
                Q(assessment__organization__name__icontains=q) |
                Q(assessment__organization__created_by__email__icontains=q) |
                Q(assessment__organization__created_by__first_name__icontains=q) |
                Q(assessment__organization__created_by__last_name__icontains=q) |
                Q(comment__icontains=q)
            )

        # ordering
        ordering = request.query_params.get("ordering") or "-date"
        mapping = {"id": "id", "-id": "-id", "date": "created_at", "-date": "-created_at"}
        qs = qs.order_by(mapping.get(ordering, "-created_at"))

        # paginate (50)
        from rest_framework.pagination import PageNumberPagination
        p = PageNumberPagination()
        p.page_size = 50
        page = p.paginate_queryset(qs, request)

        def row(fb: AssessmentFeedback):
            a = fb.assessment
            u = a.organization.created_by
            status_label = "Completed" if a.status == "SUBMITTED" else "Incomplete"
            return {
                "id": fb.id,
                "assessment_id": a.id,
                "created_at": fb.created_at,
                "user_id": u.id,
                "user_email": u.email,
                "organization_name": a.organization.name,
                "status": status_label,
                "review": (fb.comment or ""),
            }

        data = [row(f) for f in page]
        return p.get_paginated_response(AdminReviewListSerializer(data, many=True).data)

    @extend_schema(
        summary="Retrieve a feedback record (Admin)",
        responses={200: AdminReviewDetailSerializer, 404: OpenApiResponse},
        examples=[
            OpenApiExample(
                "Detail",
                response_only=True,
                value={
                    "id": 9,
                    "assessment_id": 42,
                    "date": "2025-11-02T10:10:00Z",
                    "user": {"id": 5, "email": "spo@x.com", "first_name": "A", "last_name": "B"},
                    "organization": {"id": 7, "name": "Acme Pvt Ltd"},
                    "status": "Completed",
                    "reasons": ["too_long"],
                    "comment": "Will finish later",
                }
            )
        ],
    )
    def retrieve(self, request, pk=None):
        fb = AssessmentFeedback.objects.select_related(
            "assessment", "assessment__organization", "assessment__organization__created_by"
        ).filter(pk=pk).first()
        if not fb:
            return Response({"detail": "Not found"}, status=404)

        a = fb.assessment
        u = a.organization.created_by
        payload = {
            "id": fb.id,
            "assessment_id": a.id,
            "created_at": fb.created_at,
            "user": {"id": u.id, "email": u.email, "first_name": u.first_name, "last_name": u.last_name},
            "organization": {"id": a.organization.id, "name": a.organization.name},
            "status": "Completed" if a.status == "SUBMITTED" else "Incomplete",
            "reasons": fb.reasons or [],
            "comment": fb.comment or "",
        }
        return Response(AdminReviewDetailSerializer(payload).data)