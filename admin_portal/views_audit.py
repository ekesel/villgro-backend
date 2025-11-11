from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiResponse

from django.utils.dateparse import parse_datetime
from django.db.models import Q

from .permissions import IsAdminRole
from admin_portal.models import ActivityLog
from admin_portal.serializers import ActivityLogSerializer

def _parse_range(params):
    f = params.get("from")
    t = params.get("to")
    fd = parse_datetime(f) if f else None
    td = parse_datetime(t) if t else None
    return (fd, td)

@extend_schema(
    tags=["Admin • Activity"],
    summary="List activity logs (filterable)",
    parameters=[
        OpenApiParameter("q", str, description="Search in help_text/object_repr"),
        OpenApiParameter("action", str, description="Action filter (CREATE/UPDATE/DELETE/M2M_ADD/M2M_REMOVE/API_HIT/...)"),
        OpenApiParameter("app_label", str),
        OpenApiParameter("model", str),
        OpenApiParameter("object_id", str),
        OpenApiParameter("actor", str, description="actor user id"),
        OpenApiParameter("from", str, description="ISO datetime"),
        OpenApiParameter("to", str, description="ISO datetime"),
        OpenApiParameter("ordering", str, description="e.g. -created_at (default)"),
        OpenApiParameter("page", int),
        OpenApiParameter("page_size", int),
    ],
    responses={200: OpenApiResponse(response=ActivityLogSerializer(many=True), description="Activity list")},
)
class ActivityListView(APIView):
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request):
        qs = ActivityLog.objects.exclude(action=ActivityLog.Action.API_HIT)
        q = request.query_params.get("q")
        if q:
            qs = qs.filter(Q(help_text__icontains=q) | Q(object_repr__icontains=q))
        for key in ["action", "app_label", "model", "object_id"]:
            val = request.query_params.get(key)
            if val: qs = qs.filter(**{key: val})
        actor = request.query_params.get("actor")
        if actor: qs = qs.filter(actor_id=actor)

        fd, td = _parse_range(request.query_params)
        if fd: qs = qs.filter(created_at__gte=fd)
        if td: qs = qs.filter(created_at__lte=td)

        ordering = request.query_params.get("ordering", "-created_at")
        qs = qs.order_by(ordering)

        # simple pagination
        page = int(request.query_params.get("page", 1) or 1)
        size = int(request.query_params.get("page_size", 20) or 20)
        start = (page - 1) * size
        end = start + size

        total = qs.count()
        items = qs[start:end]
        ser = ActivityLogSerializer(items, many=True)
        return Response({
            "count": total,
            "page": page,
            "page_size": size,
            "results": ser.data
        })

@extend_schema(
    tags=["Admin • Activity"],
    summary="Get single activity log",
    responses={200: ActivityLogSerializer},
)
class ActivityDetailView(APIView):
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request, pk):
        obj = ActivityLog.objects.get(pk=pk)
        return Response(ActivityLogSerializer(obj).data)