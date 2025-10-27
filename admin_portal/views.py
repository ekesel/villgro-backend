from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import status
from django.db import transaction
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiResponse
from django.db.models import Max
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
        summary="Bulk reorder sections",
        request={"type":"object","properties":{"orders":{"type":"array","items":{"type":"object","properties":{"id":{"type":"integer"},"order":{"type":"integer"}}}}}},
        responses={200: OpenApiResponse(description="OK")},
    )
    @action(detail=False, methods=["post"], url_path="reorder")
    def reorder(self, request):
        items = request.data.get("orders", [])
        for it in items:
            Section.objects.filter(id=it["id"]).update(order=it["order"])
        return Response({"updated": len(items)})


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
        sec = request.query_params.get("section")
        if not sec: return Response({"detail":"section is required"}, status=400)
        qs = self.get_queryset().filter(section__code=sec)
        return Response(QuestionAdminSerializer(qs, many=True).data)

    @extend_schema(
        summary="Append a visibility condition (does not replace existing)",
        request={"type":"object","properties":{"logic":{"type":"object"}}},
        responses={200: QuestionAdminSerializer},
    )
    @action(detail=True, methods=["post"], url_path="add-condition")
    def add_condition(self, request, pk=None):
        q = self.get_object()
        logic = request.data.get("logic")
        if not logic: return Response({"detail":"logic is required"}, status=400)
        # validate by running partial serializer with a single condition
        ser = QuestionAdminSerializer(q, data={"conditions":[{"logic": logic}]}, partial=True)
        ser.is_valid(raise_exception=True)
        BranchingCondition.objects.create(question=q, logic=logic)
        q.refresh_from_db()
        return Response(QuestionAdminSerializer(q).data)

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
        src = self.get_object()
        new_code = (request.data.get("new_code") or "").strip()
        if not new_code:
            return Response({"detail": "new_code is required."}, status=status.HTTP_400_BAD_REQUEST)
        if Question.objects.filter(code=new_code).exists():
            return Response({"detail": "new_code already exists."}, status=status.HTTP_400_BAD_REQUEST)

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

    @extend_schema(
        summary="Bulk reorder questions",
        request={"type":"object","properties":{"orders":{"type":"array","items":{"type":"object","properties":{"id":{"type":"integer"},"order":{"type":"integer"}}}}}},
        responses={200: OpenApiResponse(description="OK")},
    )
    @action(detail=False, methods=["post"], url_path="reorder")
    def reorder(self, request):
        items = request.data.get("orders", [])
        for it in items:
            Question.objects.filter(id=it["id"]).update(order=it["order"])
        return Response({"updated": len(items)})

    @extend_schema(
        summary="Reorder options for a choice question",
        request={"type":"object","properties":{"orders":{"type":"array","items":{"type":"object","properties":{"id":{"type":"integer"},"order":{"type":"integer"}}}}}},
        responses={200: OpenApiResponse(description="OK")},
    )
    @action(detail=True, methods=["post"], url_path="reorder-options")
    def reorder_options(self, request, pk=None):
        q = self.get_object()
        if q.type not in ["SINGLE_CHOICE","MULTI_CHOICE", "NPS"]:
            return Response({"detail":"Only for choice questions"}, status=400)
        # Add 'order' to AnswerOption model if you want persistent option order (else skip)
        items = request.data.get("orders", [])
        id_to_order = {it["id"]: it["order"] for it in items}
        for opt in AnswerOption.objects.filter(question=q, id__in=id_to_order.keys()):
            # if you add 'order' field in AnswerOption; if not, remove this feature
            setattr(opt, "order", id_to_order[opt.id])
            opt.save(update_fields=["order"])
        return Response({"updated": len(items)})