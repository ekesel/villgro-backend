# admin_portal/views_admin_users.py
import logging

from rest_framework import viewsets, status
from rest_framework.response import Response
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.http import Http404
from rest_framework.exceptions import ValidationError
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiResponse
from admin_portal.permissions import IsAdminRole
from admin_portal.serializers import AdminUserSerializerLite, AdminUserCreateSerializer
from rest_framework.pagination import PageNumberPagination

User = get_user_model()
logger = logging.getLogger(__name__)

class AdminPage(PageNumberPagination):
    page_size = 50
    max_page_size = 100

@extend_schema(tags=["Admin • Admins"])
class AdminUsersViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAdminRole]
    lookup_field = "pk"
    pagination_class = AdminPage

    def get_queryset(self):
        qs = User.objects.filter(role=User.Role.ADMIN)
        q = self.request.query_params.get("q")
        if q:
            qs = qs.filter(
                Q(email__icontains=q) |
                Q(first_name__icontains=q) |
                Q(last_name__icontains=q)
            )
        ordering = self.request.query_params.get("ordering") or "-date_joined"
        allowed = {"email","-email","first_name","-first_name","date_joined","-date_joined"}
        return qs.order_by(ordering if ordering in allowed else "-date_joined")

    def get_serializer_class(self):
        if self.action == "create":
            return AdminUserCreateSerializer
        return AdminUserSerializerLite

    @extend_schema(
        summary="List Admin users",
        parameters=[
            OpenApiParameter(name="q", required=False, type=str),
            OpenApiParameter(name="ordering", required=False, type=str),
        ],
        responses={200: AdminUserSerializerLite(many=True)},
    )
    def list(self, *args, **kwargs):
        return super().list(*args, **kwargs)

    @extend_schema(
        summary="Create Admin",
        request=AdminUserCreateSerializer,
        responses={201: AdminUserSerializerLite, 400: OpenApiResponse},
    )
    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        try:
            ser.is_valid(raise_exception=True)
        except ValidationError as exc:
            logger.info("Admin create validation failed for %s: %s", request.data.get("email"), exc.detail)
            return Response(
                {"message": "Please fix the highlighted fields.", "errors": exc.detail},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception:
            logger.exception("Unexpected error validating admin user create payload")
            return Response(
                {"message": "We could not create the admin user right now. Please try again later."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        try:
            user = ser.save()
        except Exception:
            logger.exception("Failed to create admin user for %s", ser.validated_data.get("email"))
            return Response(
                {"message": "We could not create the admin user right now. Please try again later."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        out = AdminUserSerializerLite(user).data
        return Response(out, status=status.HTTP_201_CREATED)

    @extend_schema(
        summary="Retrieve Admin",
        responses={200: AdminUserSerializerLite, 404: OpenApiResponse},
    )
    def retrieve(self, *args, **kwargs):
        return super().retrieve(*args, **kwargs)

    @extend_schema(
        summary="Update Admin (toggle is_active, names, phone)",
        responses={200: AdminUserSerializerLite},
    )
    def partial_update(self, request, *args, **kwargs):
        try:
            u = self.get_object()
        except Http404:
            logger.info("Admin user not found for update: %s", kwargs.get(self.lookup_field))
            return Response({"message": "Admin user not found."}, status=status.HTTP_404_NOT_FOUND)
        except Exception:
            logger.exception("Failed to load admin user for update %s", kwargs.get(self.lookup_field))
            return Response(
                {"message": "We could not fetch the admin user right now. Please try again later."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        try:
            for field in ("first_name", "last_name", "phone", "is_active"):
                if field in request.data:
                    setattr(u, field, request.data[field])
            u.save()
        except ValidationError as exc:
            logger.info("Admin user update validation failed for %s: %s", u.pk, exc.detail)
            return Response(
                {"message": "Please fix the highlighted fields.", "errors": exc.detail},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception:
            logger.exception("Failed to update admin user %s", u.pk)
            return Response(
                {"message": "We could not update the admin user right now. Please try again later."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(AdminUserSerializerLite(u).data)

    @extend_schema(
        summary="Delete Admin",
        responses={204: OpenApiResponse},
    )
    def destroy(self, request, *args, **kwargs):
        try:
            obj = self.get_object()
        except Http404:
            logger.info("Admin user not found for delete: %s", kwargs.get(self.lookup_field))
            return Response({"message": "Admin user not found."}, status=status.HTTP_404_NOT_FOUND)
        except Exception:
            logger.exception("Failed to load admin user for delete %s", kwargs.get(self.lookup_field))
            return Response(
                {"message": "We could not fetch the admin user right now. Please try again later."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        try:
            obj.delete()
        except Exception:
            logger.exception("Failed to delete admin user %s", obj.pk)
            return Response(
                {"message": "We could not delete the admin user right now. Please try again later."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(status=204)
