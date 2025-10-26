from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.contrib.auth import get_user_model
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiResponse, OpenApiExample
from admin_portal.permissions import IsAdminRole
from admin_portal.serializers import (
    AdminSPOListSerializer, AdminSPOCreateSerializer, AdminSPOUpdateSerializer
)
from django.db.models import Q

User = get_user_model()

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
        ser.is_valid(raise_exception=True)
        user = ser.save()
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
        return super().update(request, *args, **kwargs)

    @extend_schema(
        summary="Partial update SPO & Organization (PATCH)",
        request=AdminSPOUpdateSerializer,
        responses={200: AdminSPOListSerializer},
    )
    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    @extend_schema(
        summary="Delete SPO (and its Organization)",
        responses={204: OpenApiResponse(description="Deleted"), 404: OpenApiResponse(description="Not found")},
    )
    def destroy(self, request, *args, **kwargs):
        user = self.get_object()
        if hasattr(user, "organization"):
            user.organization.delete()
        user.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["patch"], url_path="toggle-status")
    @extend_schema(
        summary="Enable/disable SPO",
        description="Flips `is_active` for the SPO.",
        responses={200: AdminSPOListSerializer},
        examples=[OpenApiExample("Response", value={"id": 12, "is_active": False})],
    )
    def toggle_status(self, request, pk=None):
        user = self.get_object()
        user.is_active = not user.is_active
        user.save(update_fields=["is_active"])
        return Response(AdminSPOListSerializer(user).data)