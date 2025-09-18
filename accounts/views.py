from rest_framework.views import APIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken
from drf_spectacular.utils import extend_schema, OpenApiExample
from rest_framework_simplejwt.views import TokenRefreshView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from accounts.serializers import SPOSignupStartSerializer, SPOProfileCompleteSerializer, \
    EmailTokenObtainPairSerializer, LogoutSerializer, ForgotPasswordSerializer, VerifyCodeSerializer, ResetPasswordSerializer
from organizations.utils import get_or_create_progress

class SPOSignupStartView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        request=SPOSignupStartSerializer,
        responses={201: dict},
        examples=[
            OpenApiExample(
                "Start signup payload",
                value={"email":"founder@startup.com","password":"StrongPass123!","confirm_password":"StrongPass123!", "agree_to_terms": True},
                request_only=True
            ),
            OpenApiExample(
                "Start signup response",
                value={
                    "message": "Account created. Complete profile next.",
                    "user": {"email":"founder@startup.com","role":"SPO"},
                    "tokens": {"access":"<jwt>","refresh":"<jwt>"}
                },
                response_only=True
            ),
        ],
    )
    def post(self, request):
        ser = SPOSignupStartSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = ser.save()
        refresh = RefreshToken.for_user(user)
        return Response(
            {
                "message": "Account created. Complete profile next.",
                "user": {"email": user.email, "role": user.role},
                "tokens": {"access": str(refresh.access_token), "refresh": str(refresh)},
            },
            status=status.HTTP_201_CREATED
        )
    

class SPOSignupCompleteView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=SPOProfileCompleteSerializer,
        responses={201: dict},
        examples=[
            OpenApiExample(
                "Complete profile payload",
                value={
                    "org_name":"GreenLeaf Labs Pvt Ltd",
                    "registration_type":"PRIVATE_LTD",
                    "date_of_incorporation":"2021-06-01",
                    "gst_number":"27ABCDE1234F1Z5",
                    "cin_number":"U12345MH2021PTC000000"
                },
                request_only=True
            ),
            OpenApiExample(
                "Complete profile response",
                value={"message":"Profile completed.","organization":{"name":"GreenLeaf Labs Pvt Ltd","registration_type":"PRIVATE_LTD"}},
                response_only=True
            ),
        ],
    )
    def post(self, request):
        ser = SPOProfileCompleteSerializer(data=request.data, context={"request": request})
        ser.is_valid(raise_exception=True)
        org = ser.save()
        prog = get_or_create_progress(request.user)
        if prog.current_step < 2:   # don’t move backward
            prog.current_step = 2
            prog.is_complete = False
            prog.save(update_fields=["current_step", "is_complete", "updated_at"])
        return Response(
            {
                "message": "Profile (step 1) saved.",
                "organization": {"name": org.name, "registration_type": org.registration_type},
                "has_completed_profile": bool(prog.is_complete),
                "onboarding": {
                    "current_step": prog.current_step,
                    "is_complete": prog.is_complete,
                },
            },
            status=status.HTTP_201_CREATED
        )
    
class LoginView(TokenObtainPairView):
    permission_classes = [AllowAny]
    serializer_class = EmailTokenObtainPairSerializer

    @extend_schema(
        request=EmailTokenObtainPairSerializer,
        responses={200: dict},
        examples=[
            OpenApiExample(
                "Login payload (email + password)",
                value={"email": "founder@startup.com", "password": "StrongPass123!"},
                request_only=True
            ),
            OpenApiExample(
                "Login success response",
                value={
                    "access": "<jwt_access>",
                    "refresh": "<jwt_refresh>",
                    "user": {
                        "email": "founder@startup.com",
                        "first_name": "Asha",
                        "last_name": "Verma",
                        "role": "SPO",
                        "has_completed_profile": True
                    }
                },
                response_only=True
            ),
        ],
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


class RefreshView(TokenRefreshView):
    permission_classes = [AllowAny]

class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=LogoutSerializer,
        responses={205: dict},
        examples=[
            OpenApiExample(
                "Logout payload",
                value={"refresh": "<jwt_refresh_token>"},
                request_only=True
            ),
            OpenApiExample(
                "Logout success response",
                value={"message": "Logout successful. Token blacklisted."},
                response_only=True
            ),
        ],
    )
    def post(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({"message": "Logout successful. Token blacklisted."}, status=205)

class ForgotPasswordView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = ForgotPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({"message": "If the email exists, a reset code has been sent."})


class VerifyCodeView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = VerifyCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response({"message": "Code verified."})


class ResetPasswordView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({"message": "Password reset successful."})