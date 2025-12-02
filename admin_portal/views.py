from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import status
from django.db import transaction
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiResponse, OpenApiExample
from django.db.models import Max
from django.db.models import Count
from admin_portal.permissions import IsAdminRole
from admin_portal.serializers import SectionAdminSerializer, QuestionAdminSerializer
from questionnaires.models import Section, Question, AnswerOption, BranchingCondition, QuestionDimension


# ----- Sections
@extend_schema(tags=["Admin • Questionnaire • Sections"])
class SectionAdminViewSet(viewsets.ModelViewSet):
    queryset = Section.objects.all().order_by("order")
    serializer_class = SectionAdminSerializer
    permission_classes = [IsAdminRole]

    @extend_schema(
        summary="List sections (by sector)",
        description=(
            "Returns sections that have at least one question in the given sector.\n\n"
            "`sector` is **required** and is matched against `Question.sector`."
        ),
        parameters=[
            OpenApiParameter(
                name="sector",
                description="Sector code / name used on Question.sector",
                required=True,
                type=str,
            ),
        ],
        responses={200: SectionAdminSerializer(many=True)},
    )
    def list(self, request, *args, **kwargs):
        """
        GET /api/admin/sections/?sector=AGRICULTURE

        sector (query param) is mandatory. We return only sections that
        have at least one Question with Question.sector = sector.
        """
        try:
            sector = request.query_params.get("sector")
            if not sector:
                return Response(
                    {"message": "sector is required.", "errors": {"sector": ["This field is required."]}},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            qs = (
                self.get_queryset()
                .filter(questions__sector=sector)
                .distinct()
            )
            serializer = self.get_serializer(qs, many=True)
            return Response(serializer.data)
        except Exception as e:
            return Response(
                {
                    "message": "We could not fetch the sections right now. Please try again later.",
                    "errors": str(e),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        summary="Bulk reorder sections",
        request={
            "type": "object",
            "properties": {
                "orders": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "order": {"type": "integer"},
                        },
                    },
                }
            },
        },
        responses={200: OpenApiResponse(description="OK")},
    )
    @action(detail=False, methods=["post"], url_path="reorder")
    def reorder(self, request):
        try:
            items = request.data.get("orders", [])
            for it in items:
                Section.objects.filter(id=it["id"]).update(order=it["order"])
            return Response({"updated": len(items)})
        except Exception as e:
            return Response(
                {
                    "message": "We could not reorder the sections right now. Please try again later.",
                    "errors": str(e),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ----- Questions
@extend_schema(tags=["Admin • Questionnaire • Questions"])
class QuestionAdminViewSet(viewsets.ModelViewSet):
    queryset = (
        Question.objects.select_related("section")
        .prefetch_related("options","dimensions","conditions")
        .order_by("order")
    )
    serializer_class = QuestionAdminSerializer
    permission_classes = [IsAdminRole]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["section__code","type","required"]

    def _save_with_auto_order(self, serializer):
        """
        Save serializer, ensuring 'order' is unique within section.
        If missing or colliding, set order = (max_order_in_section + 1).
        """
        try:
            vd = serializer.validated_data
            section = vd.get("section") or getattr(getattr(serializer.instance, "section", None), "pk", None)
            if not section:
                # let serializer validation complain if section is required
                return serializer.save()

            provided_order = vd.get("order", None)

            # current max in this section
            max_order = (
                Question.objects.filter(section=section).aggregate(m=Max("order"))["m"] or 0
            )

            # collision or not provided -> append to end
            if provided_order is None or Question.objects.filter(section=section, order=provided_order).exists():
                return serializer.save(order=max_order + 1)

            # no collision -> keep as-is
            return serializer.save()
        except Exception as e:
            return Response(
                {"message": "We could not save with auto ordering", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # --- create --------------------------------------------------------------
    def perform_create(self, serializer):
        self._save_with_auto_order(serializer)

    @extend_schema(
        summary="List questions by section code",
        parameters=[OpenApiParameter(name="section", required=True, type=str)],
        responses={200: QuestionAdminSerializer(many=True)},
    )
    @action(detail=False, methods=["get"], url_path="by-section")
    def by_section(self, request):
        try:
            sec = request.query_params.get("section")
            if not sec: return Response({"message":"section is required", "errors": {}}, status=400)
            qs = self.get_queryset().filter(section__code=sec)
            return Response(QuestionAdminSerializer(qs, many=True).data)
        except Exception as e:
            return Response(
                {"message": "We could not fetch the questions right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        summary="Append a visibility condition (does not replace existing)",
        request={"type":"object","properties":{"logic":{"type":"object"}}},
        responses={200: QuestionAdminSerializer},
    )
    @action(detail=True, methods=["post"], url_path="add-condition")
    def add_condition(self, request, pk=None):
        try:
            q = self.get_object()
            logic = request.data.get("logic")
            if not logic: return Response({"message":"logic is required", "errors": {}}, status=400)
            # validate by running partial serializer with a single condition
            ser = QuestionAdminSerializer(q, data={"conditions":[{"logic": logic}]}, partial=True)
            ser.is_valid(raise_exception=True)
            BranchingCondition.objects.create(question=q, logic=logic)
            q.refresh_from_db()
            return Response(QuestionAdminSerializer(q).data)
        except Exception as e:
            return Response(
                {"message": "We could not add the condition right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        summary="Duplicate a question (deep copy children)",
        request={"type":"object","properties":{"new_code":{"type":"string"}}},
        responses={201: QuestionAdminSerializer},
    )
    @action(detail=True, methods=["post"], url_path="duplicate")
    def duplicate(self, request, pk=None):
        """
        Duplicate a question including options/dimensions/conditions.
        Avoids reverse set assignment; uses create/bulk_create instead.
        """
        try:
            src = self.get_object()
            new_code = (request.data.get("new_code") or "").strip()
            if not new_code:
                return Response({"message": "new_code is required.", "errors": {}}, status=status.HTTP_400_BAD_REQUEST)
            if Question.objects.filter(code=new_code).exists():
                return Response({"message": "new_code already exists.", "errors": {}}, status=status.HTTP_400_BAD_REQUEST)

            with transaction.atomic():
                q = Question.objects.create(
                    section=src.section,
                    code=new_code,
                    text=src.text,
                    help_text=src.help_text,
                    type=src.type,
                    required=src.required,
                    order=(src.order or 0) + 1,
                    max_score=src.max_score,
                    weight=src.weight,
                    is_active=getattr(src, "is_active", True),
                )

                # Clone options
                opts = [
                    AnswerOption(
                        question=q,
                        label=o.label,
                        value=o.value,
                        points=o.points,
                    )
                    for o in src.options.all()
                ]
                if opts:
                    AnswerOption.objects.bulk_create(opts)

                # Clone dimensions
                dims = [
                    QuestionDimension(
                        question=q,
                        code=d.code,
                        label=d.label,
                        min_value=d.min_value,
                        max_value=d.max_value,
                        points_per_unit=d.points_per_unit,
                        weight=d.weight,
                    )
                    for d in src.dimensions.all()
                ]
                if dims:
                    QuestionDimension.objects.bulk_create(dims)

                # Clone conditions
                conds = [
                    BranchingCondition(
                        question=q,
                        logic=c.logic,
                    )
                    for c in src.conditions.all()
                ]
                if conds:
                    BranchingCondition.objects.bulk_create(conds)

            return Response(QuestionAdminSerializer(q).data, status=status.HTTP_201_CREATED)
        except Exception as e:
            return Response(
                {"message": "We could not duplicate the question right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        summary="Bulk reorder questions",
        request={"type":"object","properties":{"orders":{"type":"array","items":{"type":"object","properties":{"id":{"type":"integer"},"order":{"type":"integer"}}}}}},
        responses={200: OpenApiResponse(description="OK")},
    )
    @action(detail=False, methods=["post"], url_path="reorder")
    def reorder(self, request):
        try:
            items = request.data.get("orders", [])
            for it in items:
                Question.objects.filter(id=it["id"]).update(order=it["order"])
            return Response({"updated": len(items)})
        except Exception as e:
            return Response(
                {"message": "We could not reorder the questions right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        summary="Reorder options for a choice question",
        request={"type":"object","properties":{"orders":{"type":"array","items":{"type":"object","properties":{"id":{"type":"integer"},"order":{"type":"integer"}}}}}},
        responses={200: OpenApiResponse(description="OK")},
    )
    @action(detail=True, methods=["post"], url_path="reorder-options")
    def reorder_options(self, request, pk=None):
        try:
            q = self.get_object()
            if q.type not in ["SINGLE_CHOICE","MULTI_CHOICE", "NPS"]:
                return Response({"message":"Only for choice questions", "errors": {}}, status=400)
            # Add 'order' to AnswerOption model if you want persistent option order (else skip)
            items = request.data.get("orders", [])
            id_to_order = {it["id"]: it["order"] for it in items}
            for opt in AnswerOption.objects.filter(question=q, id__in=id_to_order.keys()):
                # if you add 'order' field in AnswerOption; if not, remove this feature
                setattr(opt, "order", id_to_order[opt.id])
                opt.save(update_fields=["order"])
            return Response({"updated": len(items)})
        except Exception as e:
            return Response(
                {"message": "We could not reorder the options right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        
    @action(detail=False, methods=["get"], url_path="sector-summary")
    @extend_schema(
        summary="Question counts per sector and section",
        description=(
            "Returns aggregated counts of questions per sector, including:\n"
            "- total_questions\n"
            "- impact_questions (section.code = 'IMPACT')\n"
            "- risk_questions (section.code = 'RISK')\n"
            "- return_questions (section.code = 'RETURN')\n\n"
            "Questions with null/blank sector are grouped under 'OTHERS'."
        ),
        responses={
            200: OpenApiResponse(
                description="Per-sector question summary",
                examples=[
                    OpenApiExample(
                        "Example payload",
                        value=[
                            {
                                "sector": "AGRICULTURE",
                                "total_questions": 16,
                                "impact_questions": 16,
                                "risk_questions": 16,
                                "return_questions": 16,
                            },
                            {
                                "sector": "OTHERS",
                                "total_questions": 10,
                                "impact_questions": 4,
                                "risk_questions": 3,
                                "return_questions": 3,
                            },
                        ],
                        response_only=True,
                    )
                ],
            )
        },
    )
    def sector_summary(self, request):
        """
        GET /api/admin/questions/sector-summary/

        Drives the admin Questions page cards:
        - Agriculture, Waste management / recycling, Livelihood Creation, Health, Others, etc.
        """
        try:
            # base queryset; you can add filters (e.g., is_active=True) if needed
            qs = self.get_queryset()

            # aggregate by sector + section code
            # result rows: {"sector": "...", "section__code": "IMPACT", "count": N}
            rows = (
                qs.values("sector", "section__code")
                  .annotate(count=Count("id"))
                  .order_by()  # no ordering at DB level
            )

            sector_stats = {}

            for row in rows:
                raw_sector = row["sector"]
                section_code = (row["section__code"] or "").upper()
                count = row["count"]

                # Treat null/blank as OTHERS
                sector = raw_sector.strip() if isinstance(raw_sector, str) else raw_sector
                if not sector:
                    sector = "OTHERS"

                if sector not in sector_stats:
                    sector_stats[sector] = {
                        "sector": sector,
                        "total_questions": 0,
                        "impact_questions": 0,
                        "risk_questions": 0,
                        "return_questions": 0,
                    }

                sector_stats[sector]["total_questions"] += count

                if section_code == "IMPACT":
                    sector_stats[sector]["impact_questions"] += count
                elif section_code == "RISK":
                    sector_stats[sector]["risk_questions"] += count
                elif section_code == "RETURN":
                    sector_stats[sector]["return_questions"] += count

            # Turn dict -> list
            data = list(sector_stats.values())

            # Optional: sort by sector name for stable UI
            data.sort(key=lambda x: x["sector"])

            return Response(data, status=status.HTTP_200_OK)

        except Exception as e:
            return Response(
                {
                    "message": "We could not fetch the sector question summary right now. Please try again later.",
                    "errors": str(e),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )