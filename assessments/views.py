from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from drf_spectacular.utils import extend_schema, OpenApiResponse, OpenApiParameter, OpenApiExample
from rest_framework import status
from django.utils import timezone
from django.conf import settings
from django.shortcuts import get_object_or_404

from assessments.models import Assessment, Answer
from assessments.serializers import (
    AssessmentSerializer,
    SectionSerializer,
    QuestionSerializer,
    AnswerUpsertSerializer,
)
from questionnaires.models import Section, Question
from assessments.services import build_answers_map, visible_questions_for_section, compute_progress

ASSESSMENT_COOLDOWN_DAYS = int(getattr(settings, "ASSESSMENT_COOLDOWN_DAYS", 180))


class StartAssessmentView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Start or resume an assessment",
        description="Starts a new draft or resumes an existing draft. Returns 403 if cooldown is active.",
        responses={
            201: AssessmentSerializer,
            200: AssessmentSerializer,
            403: OpenApiResponse(description="Cooldown period active"),
        },
    )
    def post(self, request):
        org = request.user.organization
        draft = org.assessments.filter(status="DRAFT").first()
        if draft:
            return Response(AssessmentSerializer(draft).data)

        last = org.assessments.filter(status="SUBMITTED").first()
        if last and last.cooldown_until and last.cooldown_until > timezone.now():
            return Response(
                {"detail": f"Next attempt available on {last.cooldown_until.isoformat()}"},
                status=403,
            )

        a = Assessment.objects.create(organization=org)
        return Response(AssessmentSerializer(a).data, status=201)


class CurrentAssessmentView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get current draft assessment",
        responses={
            200: AssessmentSerializer,
            404: OpenApiResponse(description="No active assessment"),
        },
    )
    def get(self, request):
        org = request.user.organization
        draft = org.assessments.filter(status="DRAFT").first()
        if not draft:
            return Response({"detail": "No active assessment"}, status=404)
        return Response(AssessmentSerializer(draft).data)


class SectionsView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="List sections with progress",
        responses={200: OpenApiResponse(description="List of sections with progress")},
    )
    def get(self, request, pk):
        assessment = get_object_or_404(Assessment, pk=pk, organization=request.user.organization)
        progress = compute_progress(assessment)
        ser = SectionSerializer(
            Section.objects.all(),
            many=True,
            context={"progress_by_section": progress["by_section"]},
        )
        return Response(
            {"sections": ser.data, "progress": {"answered": progress["answered"], "required": progress["required"]}}
        )


class QuestionsView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get visible questions in a section",
        parameters=[OpenApiParameter(name="section", required=True, type=str)],
        responses={200: OpenApiResponse(description="Section questions with answers")},
    )
    def get(self, request, pk):
        section_code = request.query_params.get("section")
        assessment = get_object_or_404(Assessment, pk=pk, organization=request.user.organization)
        sec = get_object_or_404(Section, code=section_code)
        visible = visible_questions_for_section(assessment, sec)
        answers_map = build_answers_map(assessment)
        ser = QuestionSerializer(visible, many=True, context={"answers_map": answers_map})
        return Response({"section": sec.code, "questions": ser.data})


class SaveAnswersView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Save answers (bulk upsert)",
        request=AnswerUpsertSerializer(many=True),
        responses={200: OpenApiResponse(description="Updated progress counters")},
    )
    def patch(self, request, pk):
        assessment = get_object_or_404(
            Assessment, pk=pk, organization=request.user.organization, status="DRAFT"
        )
        serializer = AnswerUpsertSerializer(data=request.data.get("answers", []), many=True)
        serializer.is_valid(raise_exception=True)

        for item in serializer.validated_data:
            q_code = item["question"]
            data = item["data"]
            try:
                q = Question.objects.get(code=q_code)
            except Question.DoesNotExist:
                return Response(
                    {"detail": f"Invalid question code {q_code}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            Answer.objects.update_or_create(assessment=assessment, question=q, defaults={"data": data})

        assessment.progress = compute_progress(assessment)
        assessment.save(update_fields=["progress"])
        return Response({"progress": assessment.progress})


class SubmitAssessmentView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Submit assessment and compute scores",
        responses={200: AssessmentSerializer, 400: OpenApiResponse(description="Missing required answers")},
    )
    def post(self, request, pk):
        assessment = get_object_or_404(
            Assessment, pk=pk, organization=request.user.organization, status="DRAFT"
        )
        progress = compute_progress(assessment)
        missing = [sec for sec, stats in progress["by_section"].items() if stats["answered"] < stats["required"]]
        if missing:
            return Response({"detail": "Missing answers", "sections": missing}, status=400)

        # scoring (simplified)
        scores = {"sections": {}, "overall": 0}
        total = 0
        count = 0
        answers_map = build_answers_map(assessment)
        for sec in Section.objects.all():
            visible = visible_questions_for_section(assessment, sec)
            if not visible:
                continue
            sec_score = 0
            sec_count = 0
            for q in visible:
                ans = answers_map.get(q.code)
                if not ans:
                    continue
                points = 0
                if q.type == "SINGLE_CHOICE":
                    val = ans.get("value")
                    opt = q.options.filter(value=val).first()
                    if opt:
                        points = float(opt.points)
                elif q.type == "MULTI_CHOICE":
                    vals = set(ans.get("values", []))
                    for opt in q.options.all():
                        if opt.value in vals:
                            points += float(opt.points)
                elif q.type in ["SLIDER", "RATING"]:
                    val = ans.get("value")
                    if val is not None:
                        points = float(val)
                elif q.type == "MULTI_SLIDER":
                    vals = ans.get("values", {})
                    for d in q.dimensions.all():
                        if d.code in vals:
                            points += float(vals[d.code]) * float(d.points_per_unit) * float(d.weight)
                sec_score += points * float(q.weight)
                sec_count += 1
            if sec_count > 0 and sec.code != "FEEDBACK":
                avg = sec_score / sec_count
                scores["sections"][sec.code] = round(avg, 2)
                total += avg
                count += 1
        scores["overall"] = round(total / count, 2) if count else 0

        assessment.status = "SUBMITTED"
        assessment.submitted_at = timezone.now()
        assessment.cooldown_until = timezone.now() + timezone.timedelta(days=ASSESSMENT_COOLDOWN_DAYS)
        assessment.scores = scores
        assessment.save(update_fields=["status", "submitted_at", "cooldown_until", "scores"])
        return Response(AssessmentSerializer(assessment).data)


class ResultsView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Get submitted assessment results", responses={200: AssessmentSerializer, 404: OpenApiResponse})
    def get(self, request, pk):
        assessment = get_object_or_404(
            Assessment, pk=pk, organization=request.user.organization, status="SUBMITTED"
        )
        return Response(AssessmentSerializer(assessment).data)


class HistoryView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="List submitted assessment attempts", responses={200: AssessmentSerializer(many=True)})
    def get(self, request):
        org = request.user.organization
        assessments = org.assessments.filter(status="SUBMITTED")
        return Response(AssessmentSerializer(assessments, many=True).data)