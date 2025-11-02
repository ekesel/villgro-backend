# assessments/views_feedback.py
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework import status
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiResponse, OpenApiExample
from assessments.models import Assessment, AssessmentFeedback
from assessments.serializers import AssessmentFeedbackSerializer

@extend_schema(
    tags=["SPO • Feedback"],
    operation_id="feedback_meta",
    summary="Feedback meta options",
    description=(
        "Returns the list of feedback reasons (key + label) sourced from "
        "`AssessmentFeedback.Reason` choices. Use this to render the SPO feedback popup."
    ),
    responses={200: OpenApiResponse(description="OK")},
    examples=[
        OpenApiExample(
            "Example",
            value={
                "reasons": [
                    {"key": "hard_to_understand", "label": "Questions were difficult to understand"},
                    {"key": "too_long", "label": "The questionnaire is too long"},
                    {"key": "irrelevant", "label": "Questions were irrelevant"},
                    {"key": "come_back_later", "label": "I will come back and complete it later"},
                    {"key": "other", "label": "Other"},
                ]
            },
            response_only=True,
        )
    ],
)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def feedback_meta(request):
    reasons = [{"key": key, "label": label} for key, label in AssessmentFeedback.Reason.choices]
    return Response({"reasons": reasons}, status=200)

class FeedbackView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["SPO • Feedback"],
        summary="Submit feedback for an assessment (SPO)",
        description="Called when SPO leaves mid-assessment or after completion. Upserts per assessment.",
        request=AssessmentFeedbackSerializer,
        responses={200: AssessmentFeedbackSerializer, 201: AssessmentFeedbackSerializer, 400: OpenApiResponse},
        examples=[
            OpenApiExample(
                "Submit feedback",
                request_only=True,
                value={"assessment": 123, "reasons": ["too_long","come_back_later"], "comment": "Will finish later"}
            )
        ],
    )
    def post(self, request):
        aid = request.data.get("assessment")
        if not aid:
            return Response({"assessment": ["This field is required."]}, status=400)

        a = get_object_or_404(Assessment, pk=aid)
        if a.organization.created_by_id != request.user.id:
            return Response({"assessment": ["Not allowed for this assessment."]}, status=400)

        # UPSERT
        inst, _ = AssessmentFeedback.objects.get_or_create(assessment=a)
        ser = AssessmentFeedbackSerializer(
            inst, data=request.data, partial=True, context={"request": request}
        )
        ser.is_valid(raise_exception=True)
        ser.save(assessment=a)
        return Response(ser.data, status=status.HTTP_200_OK)

    @extend_schema(
        tags=["SPO • Feedback"],
        summary="Get feedback (if any) for an assessment (SPO)",
        parameters=[OpenApiParameter(name="assessment_id", required=True, type=int)],
        responses={200: AssessmentFeedbackSerializer, 404: OpenApiResponse(description="No feedback found")},
    )
    def get(self, request):
        aid = request.query_params.get("assessment_id")
        if not aid:
            return Response({"detail": "assessment_id is required"}, status=400)
        a = get_object_or_404(Assessment, pk=aid, organization=request.user.organization)
        fb = AssessmentFeedback.objects.filter(assessment=a).order_by("-created_at").first()

        # if none exists, return an empty shape the frontend/test expects
        if not fb:
            return Response(
                {
                    "assessment": a.id,
                    "reasons": [],
                    "comment": "",
                    "created_at": None,
                },
                status=200,
            )

        # serialize a SINGLE instance (not the manager/queryset)
        ser = AssessmentFeedbackSerializer(fb, context={"request": request})
        return Response(ser.data, status=200)