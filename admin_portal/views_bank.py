from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q
from drf_spectacular.utils import (
    extend_schema, OpenApiParameter, OpenApiResponse, OpenApiExample
)

from admin_portal.permissions import IsAdminRole
from admin_portal.serializers import BankAdminSerializer
from banks.models import Bank

@extend_schema(tags=["Admin • Banks"])
class BankAdminViewSet(viewsets.ModelViewSet):
    """
    Admin-only CRUD for banks with simple filters:
      - ?status=ACTIVE|INACTIVE
      - ?q=...  (search name/email/phone/contact_person)
      - ?ordering=name|-name (default: name)
    """
    serializer_class = BankAdminSerializer
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get_queryset(self):
        qs = Bank.objects.all()
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)

        q = self.request.query_params.get("q")
        if q:
            qs = qs.filter(
                Q(name__icontains=q) |
                Q(contact_person__icontains=q) |
                Q(contact_email__icontains=q) |
                Q(contact_phone__icontains=q)
            )

        ordering = self.request.query_params.get("ordering") or "name"
        return qs.order_by(ordering)

    # ---- Swagger bits ----
    @extend_schema(
        summary="List banks (admin)",
        parameters=[
            OpenApiParameter(
                name="status",
                description="Filter by status",
                required=False,
                type=str,
                enum=["ACTIVE", "INACTIVE"],
            ),
            OpenApiParameter(
                name="q",
                description="Free-text search in name, contact person, email, phone",
                required=False,
                type=str,
                examples=[
                    OpenApiExample("Search by name", value="acme"),
                    OpenApiExample("Search by email domain", value="gmail.com"),
                    OpenApiExample("Search by contact person", value="john"),
                ],
            ),
            OpenApiParameter(
                name="ordering",
                description="Sort results",
                required=False,
                type=str,
                enum=["name", "-name", "created_at", "-created_at"],
                examples=[
                    OpenApiExample("A → Z", value="name"),
                    OpenApiExample("Z → A", value="-name"),
                    OpenApiExample("Old → New", value="created_at"),
                    OpenApiExample("New → Old", value="-created_at"),
                ],
            ),
        ],
        responses={200: BankAdminSerializer(many=True)},
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(summary="Create bank", responses={201: BankAdminSerializer})
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    @extend_schema(summary="Retrieve bank", responses={200: BankAdminSerializer})
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @extend_schema(summary="Update bank", responses={200: BankAdminSerializer})
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    @extend_schema(summary="Partial update bank", responses={200: BankAdminSerializer})
    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    @extend_schema(summary="Delete bank", responses={204: OpenApiResponse(description="Deleted")})
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)