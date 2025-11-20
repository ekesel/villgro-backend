from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q
from drf_spectacular.utils import (
    extend_schema, OpenApiParameter, OpenApiResponse, OpenApiExample
)
from rest_framework.exceptions import ValidationError
from admin_portal.permissions import IsAdminRole
from admin_portal.serializers import BankAdminSerializer
from banks.models import Bank
from questionnaires.utils import _build_validation_message
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
        try:
            return super().list(request, *args, **kwargs)
        except Exception as e:
            return Response(
                {"message": "We could not fetch the banks right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        summary="Create bank (also creates linked BANK_USER)",
        description=(
            "Creates a Bank and a linked BANK_USER.\n\n"
            "**User mapping**:\n"
            "- BANK_USER.email ← `contact_email`\n"
            "- BANK_USER.first_name ← `contact_person`\n"
            "- BANK_USER.phone ← `contact_phone`\n"
            "- BANK_USER.password ← `password` (write-only)\n"
        ),
        request={
            "type": "object",
            "required": ["name", "contact_email", "password"],
            "properties": {
                "name": {"type": "string", "example": "Acme Bank"},
                "contact_person": {"type": "string", "example": "Jane Doe"},
                "contact_email": {"type": "string", "format": "email", "example": "ops@acmebank.com"},
                "contact_phone": {"type": "string", "example": "9876543210"},
                "status": {
                    "type": "string",
                    "enum": ["ACTIVE", "INACTIVE"],
                    "default": "ACTIVE"
                },
                "notes": {"type": "string", "example": "North region partner"},
                "password": {
                    "type": "string",
                    "writeOnly": True,
                    "description": "Password for the created BANK_USER (mapped from payload).",
                    "example": "StrongPass123!"
                }
            }
        },
        responses={
            201: OpenApiResponse(
                description="Created",
                response=BankAdminSerializer,
                examples=[
                    OpenApiExample(
                        "Create response (truncated)",
                        value={
                            "id": 7,
                            "name": "Acme Bank",
                            "contact_person": "Jane Doe",
                            "contact_email": "ops@acmebank.com",
                            "contact_phone": "9876543210",
                            "status": "ACTIVE",
                            "notes": "North region partner",
                            "created_at": "2025-11-11T08:10:00Z",
                            "updated_at": "2025-11-11T08:10:00Z"
                        },
                        response_only=True,
                    )
                ],
            ),
            400: OpenApiResponse(description="Validation error (e.g., weak password or duplicate contact_email as user)"),
        },
        examples=[
            OpenApiExample(
                "Create bank + BANK_USER",
                value={
                    "name": "Acme Bank",
                    "contact_person": "Jane Doe",
                    "contact_email": "bank.ops@acmebank.com",
                    "contact_phone": "9876543210",
                    "status": "ACTIVE",
                    "notes": "North region partner",
                    "password": "StrongPass123!"
                },
                request_only=True,
            )
        ],
    )
    def create(self, request, *args, **kwargs):
        try:
            return super().create(request, *args, **kwargs)
        except ValidationError as exc:
            return Response(
                {"message": _build_validation_message(exc.detail), "errors": exc.detail},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            return Response(
                {"message": "We could not create the bank right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(summary="Retrieve bank", responses={200: BankAdminSerializer})
    def retrieve(self, request, *args, **kwargs):
        try:
            return super().retrieve(request, *args, **kwargs)
        except Exception as e:
            return Response(
                {"message": "We could not fetch the bank right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_404_NOT_FOUND,
            )

    @extend_schema(summary="Update bank", responses={200: BankAdminSerializer})
    def update(self, request, *args, **kwargs):
        try:
            return super().update(request, *args, **kwargs)

        except ValidationError as exc:
            # cleanly return serializer validation errors
            return Response(
                {
                    "message": _build_validation_message(exc.detail),
                    "errors": exc.detail,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        except Exception as e:
            # fallback for unexpected errors
            return Response(
                {
                    "message": "We could not update the bank right now. Please try again later.",
                    "errors": str(e),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(summary="Partial update bank", responses={200: BankAdminSerializer})
    def partial_update(self, request, *args, **kwargs):
        try:
            return super().partial_update(request, *args, **kwargs)
        except Exception as e:
            return Response(
                {"message": "We could not update the bank right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(summary="Delete bank", responses={204: OpenApiResponse(description="Deleted")})
    def destroy(self, request, *args, **kwargs):
        try:
            return super().destroy(request, *args, **kwargs)
        except Exception as e:
            return Response(
                {"message": "We could not delete the bank right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )