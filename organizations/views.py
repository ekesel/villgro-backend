from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework import status
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema, OpenApiExample
from django.shortcuts import get_object_or_404

from organizations.models import OnboardingProgress, Organization
from organizations.serializers import (
    OnboardingProgressSerializer,
    OnboardingProgressSaveSerializer,
    OnboardingAdvanceSerializer,
    Step2Serializer, Step3Serializer, FinishSerializer
)
from organizations.utils import get_or_create_progress
from organizations.constants import INDIA_STATES

class OnboardingProgressView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        responses={200: OnboardingProgressSerializer},
        examples=[OpenApiExample(
            "Resume example",
            value={"current_step": 2, "data": {"step2":{"type_of_innovation":"Product"}}, "is_complete": False}
        )],
    )
    def get(self, request):
        prog = get_or_create_progress(request.user)
        payload = OnboardingProgressSerializer(prog).data
        payload["has_completed_profile"] = bool(prog.is_complete)
        return Response(payload)

    @extend_schema(
        request=OnboardingProgressSaveSerializer,
        responses={200: OnboardingProgressSerializer},
        examples=[OpenApiExample(
            "Save example",
            value={"current_step": 2, "data":{"step2":{"geo_scope":"Across states","top_states":["KA","MH"]}}},
            request_only=True
        )],
    )
    def patch(self, request):
        prog = get_or_create_progress(request.user)
        ser = OnboardingProgressSaveSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        prog = ser.update(prog, ser.validated_data)
        return Response(OnboardingProgressSerializer(prog).data, status=status.HTTP_200_OK)

class OnboardingAdvanceView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=OnboardingAdvanceSerializer,
        responses={200: OnboardingProgressSerializer},
        examples=[OpenApiExample("Advance to step 3", value={"to_step": 3}, request_only=True)],
    )
    def post(self, request):
        prog = get_or_create_progress(request.user)
        ser = OnboardingAdvanceSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        prog.bump_to(ser.validated_data["to_step"])
        prog.save()
        return Response(OnboardingProgressSerializer(prog).data)
    

def _get_org_and_progress(user):
    org = getattr(user, "organization", None)
    if not org:
        return None, OnboardingProgress.objects.get_or_create(user=user)[0]
    prog, _ = OnboardingProgress.objects.get_or_create(user=user)
    return org, prog

class OnboardingStep2View(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=Step2Serializer, responses={200: dict})
    def patch(self, request):
        org, prog = _get_org_and_progress(request.user)
        if not org:
            return Response({"detail": "Organization not found. Complete step 1 first."}, status=400)
        ser = Step2Serializer(data=request.data, context={"organization": org, "progress": prog})
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response({
            "message": "Step 2 saved.",
            "onboarding": {"current_step": prog.current_step, "is_complete": prog.is_complete}
        })

class OnboardingStep3View(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=Step3Serializer, responses={200: dict})
    def patch(self, request):
        org, prog = _get_org_and_progress(request.user)
        if not org:
            return Response({"detail": "Organization not found. Complete step 1 first."}, status=400)
        ser = Step3Serializer(data=request.data, context={"organization": org})
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response({
            "message": "Step 3 saved.",
            "onboarding": {"current_step": prog.current_step, "is_complete": prog.is_complete}
        })

class OnboardingFinishView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=FinishSerializer, responses={200: dict})
    def post(self, request):
        org, prog = _get_org_and_progress(request.user)
        if not org:
            return Response({"detail": "Organization not found. Complete step 1 first."}, status=400)
        ser = FinishSerializer(data={}, context={"organization": org, "progress": prog})
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response({
            "message": "Onboarding completed.",
            "onboarding": {"current_step": prog.current_step, "is_complete": prog.is_complete}
        })
    
class MetaOptionsView(APIView):
    permission_classes = [AllowAny]  # Public; or use IsAuthenticated if you prefer

    @extend_schema(responses={200: dict})
    def get(self, request):
        def to_key_label(choices):
            return [{"key": k, "label": v} for k, v in choices]

        return Response({
            "registration_types": to_key_label(Organization.RegistrationType.choices),
            "innovation_types":   to_key_label(Organization.InnovationType.choices),
            "geo_scopes":         to_key_label(Organization.GeoScope.choices),
            "focus_sectors":      to_key_label(Organization.FocusSector.choices),
            "stages":             to_key_label(Organization.OrgStage.choices),
            "impact_focus":       to_key_label(Organization.ImpactFocus.choices),
            "use_of_questionnaire": to_key_label(Organization.UseOfQuestionnaire.choices),
            "states": INDIA_STATES,
            "top_states_limit": 5
        })