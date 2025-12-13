from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from drf_spectacular.utils import extend_schema, OpenApiResponse, OpenApiParameter, OpenApiExample, inline_serializer
from rest_framework import status, serializers
from weasyprint import HTML
from django.template.loader import render_to_string
from django.http import HttpResponse
from django.utils import timezone
from admin_portal.models import AdminConfig
from django.shortcuts import get_object_or_404

from assessments.services import compute_scores
from assessments.models import Assessment, Answer
from assessments.serializers import (
    AssessmentSerializer,
    SectionSerializer,
    QuestionSerializer,
    AnswerUpsertSerializer,
)
from questionnaires.models import Section, Question
from assessments.services import build_answers_map, visible_questions_for_section, compute_progress
from assessments.services import get_control_qcodes
from questionnaires.logic import eligibility_check

class StartAssessmentView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Assessment • SPO"],
        operation_id="assessment_start_or_resume",
        summary="Start or resume an assessment",
        description=(
            "Creates a new **DRAFT** assessment for the SPO's organization **or** resumes an existing draft.\n\n"
            "If the latest submitted assessment is still in **cooldown**, returns `403` with the next allowed date."
        ),
        responses={
            201: AssessmentSerializer,
            200: AssessmentSerializer,
            403: inline_serializer(
                name="CooldownActive",
                fields={"message": serializers.CharField()}
            ),
        },
        examples=[
            OpenApiExample(
                "Draft Assessment (201)",
                value={
                    "id": 42,
                    "status": "DRAFT",
                    "started_at": "2025-09-19T12:00:00Z",
                    "submitted_at": None,
                    "cooldown_until": None,
                    "progress": {
                        "answered": 2,
                        "required": 10,
                        "percent": 20,
                        "by_section": {"IMPACT": {"answered": 1, "required": 3}},
                    },
                    "resume": {"last_section": "IMPACT"},
                    "scores": {}
                },
                response_only=True,
            ),
            OpenApiExample(
                "Cooldown active (403)",
                value={"message": "Next attempt available on 2026-01-01T00:00:00Z"},
                response_only=True,
            ),
        ],
    )
    def post(self, request):
        try:
            org = request.user.organization
            draft = org.assessments.filter(status="DRAFT").first()
            if draft:
                return Response(AssessmentSerializer(draft).data)

            last = org.assessments.filter(status="SUBMITTED").first()
            if last and last.cooldown_until and last.cooldown_until > timezone.now():
                return Response(
                    {"message": f"Next attempt available on {last.cooldown_until.isoformat()}"},
                    status=403,
                )

            a = Assessment.objects.create(organization=org)
            return Response(AssessmentSerializer(a).data, status=201)
        except Exception as e:
            return Response(
                {"message": "We could not start or resume the assessment right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class CurrentAssessmentView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Assessment • SPO"],
        operation_id="assessment_current_draft",
        summary="Get current draft assessment (with progress and resume state)",
        responses={
            200: AssessmentSerializer,
            404: inline_serializer(
                name="NoActiveAssessment",
                fields={"message": serializers.CharField()}
            ),
        },
        examples=[
            OpenApiExample(
                "Current Draft",
                value={
                    "id": 42,
                    "status": "DRAFT",
                    "progress": {
                        "answered": 4,
                        "required": 12,
                        "percent": 33,
                        "by_section": {
                            "IMPACT": {"answered": 2, "required": 3},
                            "RISK": {"answered": 1, "required": 4},
                            "RETURN": {"answered": 1, "required": 3}
                        }
                    },
                    "resume": {"last_section": "RISK"}
                },
                response_only=True,
            )
        ],
    )
    def get(self, request):
        try:
            org = request.user.organization
            draft = org.assessments.filter(status="DRAFT").first()
            if not draft:
                return Response({"message": "No active assessment", "errors": {}}, status=404)
            progress = compute_progress(draft)
            data = AssessmentSerializer(draft).data
            data["progress"] = {
                "answered": progress["answered"],
                "required": progress["required"],
                "percent": progress["percent"],
                "by_section": progress["by_section"],
            }
            data["resume"] = {"last_section": progress.get("last_section")}
            return Response(data)
        except Exception as e:
            return Response(
                {"message": "We could not fetch the current assessment right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class SectionsView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Assessment • SPO"],
        operation_id="assessment_sections",
        summary="List sections with progress",
        parameters=[
            OpenApiParameter(
                name="pk",
                description="Assessment ID",
                required=True,
                type=int,
                location=OpenApiParameter.PATH
            )
        ],
        responses={
            200: inline_serializer(
                name="SectionsWithProgress",
                fields={
                    "sections": SectionSerializer(many=True),
                    "progress": serializers.JSONField(),
                    "resume": serializers.JSONField(),
                }
            ),
            404: OpenApiResponse(description="Assessment not found"),
        },
        examples=[
            OpenApiExample(
                "Sections Response",
                value={
                    "sections": [
                        {"code": "IMPACT", "title": "Impact", "progress": {"answered": 1, "required": 3}},
                        {"code": "RISK", "title": "Risk", "progress": {"answered": 0, "required": 4}}
                    ],
                    "progress": {
                        "answered": 1,
                        "required": 7,
                        "percent": 14,
                        "by_section": {
                            "IMPACT": {"answered": 1, "required": 3},
                            "RISK": {"answered": 0, "required": 4}
                        }
                    },
                    "resume": {"last_section": "IMPACT"}
                },
                response_only=True,
            )
        ],
    )
    def get(self, request, pk):
        try:
            assessment = get_object_or_404(Assessment, pk=pk, organization=request.user.organization)
            progress = compute_progress(assessment)
            ser = SectionSerializer(
                Section.objects.all(),
                many=True,
                context={"progress_by_section": progress["by_section"]},
            )
            payload = {
                "sections": ser.data,
                "progress": {
                    "answered": progress["answered"],
                    "required": progress["required"],
                    "percent": progress["percent"],
                    "by_section": progress["by_section"],
                },
                "resume": {"last_section": progress.get("last_section")},
            }
            return Response(payload)
        except Exception as e:
            return Response(
                {"message": "We could not fetch the sections right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class QuestionsView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Assessment • SPO"],
        operation_id="assessment_section_questions",
        summary="Get visible questions in a section",
        parameters=[
            OpenApiParameter(
                name="pk",
                description="Assessment ID",
                required=True,
                type=int,
                location=OpenApiParameter.PATH
            ),
            OpenApiParameter(
                name="section",
                description="Section code (e.g., IMPACT, RISK, RETURN)",
                required=True,
                type=str,
                location=OpenApiParameter.QUERY
            ),
        ],
        responses={
            200: inline_serializer(
                name="SectionQuestionsResponse",
                fields={
                    "section": serializers.CharField(),
                    "questions": QuestionSerializer(many=True),
                }
            ),
            404: OpenApiResponse(description="Assessment or Section not found"),
        },
        examples=[
            OpenApiExample(
                "Questions Example",
                value={
                    "section": "IMPACT",
                    "questions": [
                        {
                            "code": "IMP_Q1",
                            "text": "How many beneficiaries last year?",
                            "type": "SLIDER",
                            "required": True,
                            "order": 1,
                            "answer": {"value": 8}
                        }
                    ]
                },
                response_only=True
            )
        ],
    )
    def get(self, request, pk):
        try:
            section_code = request.query_params.get("section")
            assessment = get_object_or_404(Assessment, pk=pk, organization=request.user.organization)
            sec = get_object_or_404(Section, code=section_code)
            visible = visible_questions_for_section(assessment, sec)
            answers_map = build_answers_map(assessment)
            control_set = get_control_qcodes()
            ser = QuestionSerializer(visible, many=True, context={"answers_map": answers_map, "control_set": control_set })
            return Response({"section": sec.code, "questions": ser.data})
        except Exception as e:
            return Response(
                {"message": "We could not fetch the questions right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class SaveAnswersView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Assessment • SPO"],
        operation_id="assessment_save_answers",
        summary="Save answers (bulk upsert)",
        description=(
            "Bulk upsert answers for the given assessment. Pass an array of `{question, data}` objects inside `answers`.\n\n"
            "**Note:** The server recomputes progress and updates the resume pointer (`last_section`)."
        ),
        parameters=[
            OpenApiParameter(
                name="pk",
                description="Assessment ID",
                required=True, type=int, location=OpenApiParameter.PATH
            ),
            OpenApiParameter(
                name="section",
                description="Section code you are editing; used to update resume state",
                required=False, type=str, location=OpenApiParameter.QUERY
            )
        ],
        request=inline_serializer(
            name="SaveAnswersRequest",
            fields={
                "answers": AnswerUpsertSerializer(many=True)
            }
        ),
        responses={
            200: inline_serializer(
                name="SaveAnswersResponse",
                fields={
                    "progress": serializers.JSONField(),
                    "resume": serializers.JSONField(),
                }
            ),
            400: inline_serializer(name="BadRequest", fields={"message": serializers.CharField()}),
            404: OpenApiResponse(description="Assessment not found"),
        },
        examples=[
            OpenApiExample(
                "Request Body",
                request_only=True,
                value={
                    "answers": [
                        {"question": "IMP_Q1", "data": {"value": 8}},
                        {"question": "RISK_Q2", "data": {"values": ["FRAUD", "SUPPLY"]}}
                    ]
                }
            ),
            OpenApiExample(
                "Save Answers Response",
                value={
                    "progress": {
                        "answered": 4,
                        "required": 12,
                        "percent": 33,
                        "by_section": {
                            "IMPACT": {"answered": 2, "required": 3},
                            "RISK": {"answered": 1, "required": 4},
                            "RETURN": {"answered": 1, "required": 3}
                        }
                    },
                    "resume": {"last_section": "RISK"}
                },
                response_only=True,
            )
        ],
    )
    def patch(self, request, pk):
        try:
            assessment = get_object_or_404(
                Assessment, pk=pk, organization=request.user.organization
            )
            if assessment.status != "DRAFT":
                return Response({"message": "Assessment is submitted and cannot be modified.", "errors": {}},
                                status=status.HTTP_400_BAD_REQUEST)
            serializer = AnswerUpsertSerializer(data=request.data.get("answers", []), many=True)
            serializer.is_valid(raise_exception=True)

            for item in serializer.validated_data:
                q_code = item["question"]
                data = item["data"]
                try:
                    q = Question.objects.get(code=q_code)
                except Question.DoesNotExist:
                    return Response(
                        {"message": f"Invalid question code {q_code}", "errors": {}},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                Answer.objects.update_or_create(assessment=assessment, question=q, defaults={"data": data})

            section_code = request.query_params.get("section")
            if not section_code and serializer.validated_data:
                first_q = Question.objects.filter(code=serializer.validated_data[0]["question"], is_active=True).select_related("section").first()
                if first_q:
                    section_code = first_q.section.code

            # recompute progress (+ percent)
            progress = compute_progress(assessment)
            if section_code:
                progress["last_section"] = section_code

            assessment.progress = progress
            assessment.save(update_fields=["progress"])
            return Response({
                "progress": assessment.progress,
                "resume": {"last_section": progress.get("last_section")}
            })
        except Exception as e:
            return Response(
                {"message": "We could not save the answers right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class SubmitAssessmentView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Assessment • SPO"],
        operation_id="assessment_submit",
        summary="Submit assessment and compute scores",
        description=(
            "Validates that all **required** questions are answered, computes per-section + overall scores, "
            "sets status to **SUBMITTED**, applies cooldown, and persists.\n\n"
            "Also computes loan eligibility in the background of the response."
        ),
        parameters=[
            OpenApiParameter(
                name="pk",
                description="Assessment ID",
                required=True, type=int, location=OpenApiParameter.PATH
            )
        ],
        responses={
            200: AssessmentSerializer,
            400: inline_serializer(
                name="MissingRequiredAnswers",
                fields={
                    "message": serializers.CharField(),
                    "sections": serializers.ListField(child=serializers.CharField())
                }
            ),
            404: OpenApiResponse(description="Assessment not found"),
        },
        examples=[
            OpenApiExample(
                "Missing required answers (400)",
                value={"message": "Missing answers", "sections": ["IMPACT", "RISK"]},
                response_only=True
            ),
            OpenApiExample(
                "Submitted (200)",
                value={
                    "id": 42,
                    "status": "SUBMITTED",
                    "scores": {"sections": {"IMPACT": 90, "RISK": 20, "RETURN": 88}, "overall": 66},
                    "submitted_at": "2025-11-02T09:05:00Z",
                    "cooldown_until": "2026-05-01T00:00:00Z"
                },
                response_only=True
            ),
        ],
    )
    def post(self, request, pk):
        try:
            assessment = get_object_or_404(
                Assessment, pk=pk, organization=request.user.organization, status="DRAFT"
            )
            progress = compute_progress(assessment)
            missing = [sec for sec, stats in progress["by_section"].items() if stats["answered"] < stats["required"]]
            if missing:
                return Response({"message": "Missing answers", "sections": missing, "errors": {}}, status=400)

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
                    if q.type in ["SINGLE_CHOICE", "NPS"]:
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
            scores, _breakdown = compute_scores(assessment)
            assessment.status = "SUBMITTED"
            assessment.submitted_at = timezone.now()
            config = AdminConfig.get_solo()
            assessment.cooldown_until = timezone.now() + config.get_assessment_cooldown_timedelta()
            assessment.scores = scores
            assessment.save(update_fields=["status", "submitted_at", "cooldown_until", "scores"])
            eligibility = eligibility_check(assessment)
            return Response(AssessmentSerializer(assessment).data)
        except Exception as e:
            return Response(
                {"message": "We could not submit the assessment right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_404_NOT_FOUND,
            )


class ResultsView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Assessment • SPO"],
        operation_id="assessment_results",
        summary="Get submitted assessment results",
        responses={
            200: AssessmentSerializer,
            404: OpenApiResponse(description="Assessment not found or not submitted"),
        },
    )
    def get(self, request, pk):
        try:
            assessment = get_object_or_404(
                Assessment, pk=pk, organization=request.user.organization, status="SUBMITTED"
            )
            return Response(AssessmentSerializer(assessment).data)
        except Exception as e:
            return Response(
                {"message": "We could not fetch the assessment results right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_404_NOT_FOUND,
            )


class HistoryView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Assessment • SPO"],
        operation_id="assessment_history",
        summary="List submitted assessment attempts",
        responses={200: AssessmentSerializer(many=True)},
    )
    def get(self, request):
        try:
            org = request.user.organization
            assessments = org.assessments.filter(status="SUBMITTED")
            return Response(AssessmentSerializer(assessments, many=True).data)
        except Exception as e:
            return Response(
                {"message": "We could not fetch the assessment history right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ResultsSummaryView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Assessment • SPO"],
        operation_id="assessment_results_summary",
        summary="Results summary (per section + overall)",
        responses={
            200: inline_serializer(
                name="ResultsSummary",
                fields={
                    "id": serializers.IntegerField(),
                    "overall": serializers.FloatField(),
                    "sections": serializers.ListField(
                        child=inline_serializer(
                            name="SectionScore",
                            fields={
                                "code": serializers.CharField(),
                                "score": serializers.FloatField(),
                            }
                        )
                    )
                }
            )
        },
        examples=[
            OpenApiExample(
                "Summary",
                value={
                    "id": 42,
                    "overall": 6.38,
                    "sections": [
                        {"code": "IMPACT", "score": 7.5},
                        {"code": "RISK", "score": 5.0},
                        {"code": "RETURN", "score": 6.2},
                        {"code": "SECTOR_MATURITY", "score": 6.8}
                    ]
                },
                response_only=True
            )
        ]
    )
    def get(self, request, pk):
        try:
            assessment = get_object_or_404(
                Assessment, pk=pk, organization=request.user.organization, status="SUBMITTED"
            )
            s = assessment.scores or {}
            sections = [{"code": c, "score": s["sections"][c]} for c in sorted(s.get("sections", {}).keys())]
            return Response({"id": assessment.id, "overall": s.get("overall", 0), "sections": sections})
        except Exception as e:
            return Response(
                {"message": "We could not fetch the results summary right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class SectionResultsView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Assessment • SPO"],
        operation_id="assessment_section_results_detail",
        summary="Detailed section results (question contributions)",
        parameters=[
            OpenApiParameter(
                name="pk",
                description="Assessment ID",
                required=True, type=int, location=OpenApiParameter.PATH
            ),
            OpenApiParameter(
                name="section",
                description="Section code to fetch breakdown for",
                required=True, type=str, location=OpenApiParameter.QUERY
            ),
        ],
        responses={
            200: inline_serializer(
                name="SectionBreakdown",
                fields={
                    "id": serializers.IntegerField(),
                    "section": serializers.CharField(),
                    "questions": serializers.ListField(child=serializers.JSONField())
                }
            ),
            404: OpenApiResponse(description="Assessment not found or not submitted"),
        },
    )
    def get(self, request, pk):
        try:
            section_code = request.query_params.get("section")
            assessment = get_object_or_404(
                Assessment, pk=pk, organization=request.user.organization, status="SUBMITTED"
            )
            from assessments.services import compute_scores
            _scores, breakdown = compute_scores(assessment)
            data = breakdown.get(section_code, [])
            return Response({"id": assessment.id, "section": section_code, "questions": data})
        except Exception as e:
            return Response(
                {"message": "We could not fetch the section results right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ReportPDFView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Assessment • SPO"],
        operation_id="assessment_report_pdf",
        summary="Download PDF report of submitted assessment",
        responses={200: OpenApiResponse(description="PDF binary (application/pdf)")},
        examples=[
            OpenApiExample(
                "Response headers",
                value={"Content-Disposition": 'attachment; filename="assessment-42.pdf"'},
                response_only=True,
            )
        ]
    )
    def get(self, request, pk):
        try:
            assessment = get_object_or_404(
                Assessment, pk=pk, organization=request.user.organization, status="SUBMITTED"
            )

            html_str = render_to_string("assessments/report.html", {
                "assessment": assessment,
                "scores": assessment.scores,
                "org": assessment.organization,
            })

            pdf_bytes = HTML(string=html_str).write_pdf()

            response = HttpResponse(pdf_bytes, content_type="application/pdf")
            response["Content-Disposition"] = f'attachment; filename="assessment-{assessment.id}.pdf"'
            return response
        except Exception as e:
            return Response(
                {"message": "We could not generate the PDF report right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_404_NOT_FOUND,
            )