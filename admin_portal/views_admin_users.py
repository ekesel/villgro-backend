# admin_portal/views_admin_users.py
from rest_framework import viewsets, status
from rest_framework.response import Response
from django.contrib.auth import get_user_model
from django.db.models import Q
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiResponse
from admin_portal.permissions import IsAdminRole
from admin_portal.serializers import AdminUserSerializerLite, AdminUserCreateSerializer
from rest_framework.pagination import PageNumberPagination

User = get_user_model()

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
        ser.is_valid(raise_exception=True)
        user = ser.save()
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
        u = self.get_object()
        for f in ("first_name","last_name","phone","is_active"):
            if f in request.data:
                setattr(u, f, request.data[f])
        u.save()
        return Response(AdminUserSerializerLite(u).data)

    @extend_schema(
        summary="Delete Admin",
        responses={204: OpenApiResponse},
    )
    def destroy(self, request, *args, **kwargs):
        self.get_object().delete()
        return Response(status=204)