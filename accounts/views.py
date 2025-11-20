import logging

from rest_framework.views import APIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework_simplejwt.tokens import RefreshToken
from drf_spectacular.utils import extend_schema, OpenApiExample
from rest_framework_simplejwt.views import TokenRefreshView
from rest_framework_simplejwt.views import TokenObtainPairView

from accounts.serializers import SPOSignupStartSerializer, SPOProfileCompleteSerializer, \
    EmailTokenObtainPairSerializer, LogoutSerializer, ForgotPasswordSerializer, VerifyCodeSerializer, ResetPasswordSerializer, \
    ProfileSerializer, ProfileUpdateSerializer, ChangePasswordSerializer

from organizations.utils import get_or_create_progress
from questionnaires.utils import _build_validation_message
logger = logging.getLogger(__name__)

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
        try:
            ser.is_valid(raise_exception=True)
        except ValidationError as exc:
            logger.info("SPO signup start validation failed: %s", exc.detail)
            return Response(
                {"message": "Please correct the highlighted fields.", "errors": exc.detail},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.exception("Unexpected error validating SPO signup start payload")
            return Response(
                {"message": "We could not process your signup right now. Please try again later.",
                 "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        try:
            user = ser.save()
            refresh = RefreshToken.for_user(user)
        except Exception as e:
            logger.exception("Failed to create SPO account for %s", ser.validated_data.get("email"))
            return Response(
                {"message": "We could not create your account right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
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
        responses={200: dict, 201: dict},
        examples=[
            OpenApiExample(
                "Create payload (no org yet)",
                value={
                    "org_name": "GreenLeaf Labs Pvt Ltd",
                    "registration_type": "PRIVATE_LLD",  # example; use your actual choice key
                    "date_of_incorporation": "2021-06-01",
                    "gst_number": "27ABCDE1234F1Z5",
                    "cin_number": "U12345MH2021PTC000000",
                },
                request_only=True,
            ),
            OpenApiExample(
                "Update payload (org exists) — partial allowed",
                value={
                    "gst_number": "27ABCDE1234F1Z5",
                },
                request_only=True,
            ),
        ],
    )
    def post(self, request):
        user = request.user
        instance = getattr(user, "organization", None)

        first_name = request.data.pop("first_name", "")
        last_name = request.data.pop("last_name", "")
        phone_number = request.data.pop("phone_number", "")

        if user:
            user.first_name = first_name
            user.last_name = last_name
            user.phone = phone_number
            user.save(update_fields=["first_name", "last_name", "phone"])

        # partial=True when updating existing org (accepts partial updates on POST)
        serializer = SPOProfileCompleteSerializer(
            instance=instance,
            data=request.data,
            context={"request": request},
            partial=instance is not None,
        )
        try:
            serializer.is_valid(raise_exception=True)
        except ValidationError as exc:
            logger.info("SPO profile validation failed for user %s: %s", user.id, exc.detail)
            return Response(
                {"message": "Please review the profile details.", "errors": exc.detail},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.exception("Unexpected error validating SPO profile for user %s", user.id)
            return Response(
                {"message": "We could not save the profile right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        try:
            org = serializer.save()
        except Exception as e:
            logger.exception("Failed to save SPO profile for user %s", user.id)
            return Response(
                {"message": "We could not save the profile right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # progress step handling (don’t move backward)
        prog = get_or_create_progress(user)
        if prog.current_step < 2:
            prog.current_step = 2
            prog.is_complete = False
            prog.save(update_fields=["current_step", "is_complete", "updated_at"])

        status_code = status.HTTP_200_OK if instance else status.HTTP_201_CREATED
        message = "Profile updated successfully." if instance else "Profile (step 1) saved."

        return Response(
            {
                "message": message,
                "organization": {
                    "name": org.name,
                    "registration_type": org.registration_type,
                },
                "has_completed_profile": bool(prog.is_complete),
                "onboarding": {
                    "current_step": prog.current_step,
                    "is_complete": prog.is_complete,
                },
                "user": {
                    "email": user.email,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "role": user.role,
                    "phone_number": user.phone,
                }
            },
            status=status_code,
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
        try:
            # Let DRF/JWT handle authentication normally
            return super().post(request, *args, **kwargs)

        except ValidationError as exc:
            # Example: missing fields / invalid serializer input
            return Response(
                {
                    "message": _build_validation_message(exc.detail),
                    "errors": exc.detail,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        except Exception as e:
            # Anything unexpected
            logger.exception("Unexpected error in login request")
            return Response(
                {"message": "We could not process your request right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_401_UNAUTHORIZED,
            )


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
        try:
            serializer.is_valid(raise_exception=True)
        except ValidationError as exc:
            logger.info("Logout validation failed for user %s: %s", request.user.id, exc.detail)
            return Response(
                {"message": "Please check the logout payload.", "errors": exc.detail},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.exception("Unexpected error validating logout payload for user %s", request.user.id)
            return Response(
                {"message": "We could not process your logout right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        try:
            serializer.save()
        except Exception as e:
            logger.exception("Failed to blacklist refresh token during logout for user %s", request.user.id)
            return Response(
                {"message": "We could not log you out right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response({"message": "Logout successful. Token blacklisted."}, status=205)

class ForgotPasswordView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = ForgotPasswordSerializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except ValidationError as exc:
            logger.info("Forgot password validation failed: %s", exc.detail)
            return Response(
                {"message": "Please check the email address.", "errors": exc.detail},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.exception("Unexpected error validating forgot password payload")
            return Response(
                {"message": "We could not start the reset process right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        try:
            serializer.save()
        except Exception as e:
            logger.exception("Failed to trigger forgot password flow for %s", serializer.validated_data.get("email"))
            return Response(
                {"message": "We could not start the reset process right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response({"message": "If the email exists, a reset code has been sent."})


class VerifyCodeView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = VerifyCodeSerializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except ValidationError as exc:
            logger.info("Verify code validation failed: %s", exc.detail)
            return Response(
                {"message": "The code you entered is incorrect.", "errors": exc.detail},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.exception("Unexpected error validating verification code")
            return Response(
                {"message": "We could not verify the code right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response({"message": "Code verified."})


class ResetPasswordView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = ResetPasswordSerializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except ValidationError as exc:
            logger.info("Reset password validation failed: %s", exc.detail)
            return Response(
                {"message": _build_validation_message(exc.detail), "errors": exc.detail},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.exception("Unexpected error validating reset password payload")
            return Response(
                {"message": "We could not reset the password right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        try:
            serializer.save()
        except Exception as e:
            logger.exception("Failed to reset password for user linked to token %s", serializer.validated_data.get("uid"))
            return Response(
                {"message": "We could not reset the password right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response({"message": "Password reset successful."})
    
# -------- Profile (GET+PATCH at same URL) --------
class ProfileView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: ProfileSerializer})
    def get(self, request):
        try:
            data = ProfileSerializer(request.user).data
        except Exception as e:
            logger.exception("Failed to load profile for user %s", request.user.id)
            return Response(
                {"message": "We could not fetch the profile right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(data)

    @extend_schema(request=ProfileUpdateSerializer, responses={200: ProfileSerializer})
    def patch(self, request):
        ser = ProfileUpdateSerializer(data=request.data, context={"request": request})
        try:
            ser.is_valid(raise_exception=True)
        except ValidationError as exc:
            logger.info("Profile update validation failed for user %s: %s", request.user.id, exc.detail)
            return Response(
                {"message": "Please review the profile details.", "errors": exc.detail},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.exception("Unexpected error validating profile update for user %s", request.user.id)
            return Response(
                {"message": "We could not update the profile right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        try:
            ser.save()
        except Exception as e:
            logger.exception("Failed to update profile for user %s", request.user.id)
            return Response(
                {"message": "We could not update the profile right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(ProfileSerializer(request.user).data)


# -------- Change Password --------
class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=ChangePasswordSerializer,
        responses={205: dict, 400: dict},
        examples=[
            OpenApiExample(
                "Change password payload",
                value={
                    "current_password": "OldPass123!",
                    "new_password": "NewPass123!",
                    "confirm_password": "NewPass123!"
                },
                request_only=True
            ),
            OpenApiExample(
                "Change password response",
                value={"message": "Password updated. Please log in again."},
                response_only=True
            ),
        ],
    )
    def post(self, request):
        ser = ChangePasswordSerializer(data=request.data, context={"request": request})
        try:
            ser.is_valid(raise_exception=True)
        except ValidationError as exc:
            details = exc.detail if isinstance(exc.detail, dict) else {"non_field_errors": exc.detail}
            payload = {"message": _build_validation_message(exc.detail), "errors": details}
            return Response(payload, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.exception("Unexpected error validating change password payload for user %s", request.user.id)
            return Response(
                {"message": "We could not update the password right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        try:
            ser.save()
        except Exception as e:
            logger.exception("Failed to change password for user %s", request.user.id)
            return Response(
                {"message": "We could not update the password right now. Please try again later.", "errors": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response({"message": "Password updated. Please log in again."}, status=205)
