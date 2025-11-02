# assessments/views_feedback.py
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiResponse, OpenApiExample
from assessments.models import Assessment, AssessmentFeedback
from assessments.serializers import AssessmentFeedbackSerializer

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
        fb = getattr(a, "feedback", None)
        if not fb:
            return Response({"detail": "No feedback found"}, status=404)
        ser = AssessmentFeedbackSerializer(fb, context={"request": request})
        return Response(ser.data, status=200)