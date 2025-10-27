from rest_framework.views import APIView
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiResponse
from admin_portal.permissions import IsAdminRole
from questionnaires.models import Section, Question

@extend_schema(tags=["Admin • Questionnaire • Meta"], summary="Question type dropdown")
class QuestionTypesMeta(APIView):
    permission_classes = [IsAdminRole]
    def get(self, request):
        # from your Question.TYPE_CHOICES
        types = [{"value": v, "label": l} for v, l in Question.TYPE_CHOICES]
        return Response(types)

@extend_schema(tags=["Admin • Questionnaire • Meta"], summary="Sections dropdown")
class SectionsMeta(APIView):
    permission_classes = [IsAdminRole]
    def get(self, request):
        data = list(Section.objects.order_by("order").values("id","code","title"))
        return Response(data)

@extend_schema(tags=["Admin • Questionnaire • Meta"], summary="Question codes (optional section filter)")
class QuestionCodesMeta(APIView):
    permission_classes = [IsAdminRole]
    @extend_schema(parameters=[OpenApiParameter(name="section", required=False, type=str)])
    def get(self, request):
        qs = Question.objects.all().order_by("order")
        sec = request.query_params.get("section")
        if sec: qs = qs.filter(section__code=sec)
        data = list(qs.values("code","text","type"))
        return Response(data)

@extend_schema(tags=["Admin • Questionnaire • Meta"], summary="Option values for a choice question")
class OptionValuesMeta(APIView):
    permission_classes = [IsAdminRole]
    @extend_schema(parameters=[OpenApiParameter(name="code", required=True, type=str)],
                   responses={200: OpenApiResponse(description="[{label,value}]")})
    def get(self, request):
        code = request.query_params.get("code")
        if not code: return Response({"detail":"code is required"}, status=400)
        try:
            q = Question.objects.prefetch_related("options").get(code=code)
        except Question.DoesNotExist:
            return Response({"detail":"Unknown question code"}, status=404)
        if q.type not in ["SINGLE_CHOICE","MULTI_CHOICE", "NPS"]:
            return Response([])
        return Response([{"label": o.label, "value": o.value} for o in q.options.all()])