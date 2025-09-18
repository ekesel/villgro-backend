from django.contrib.auth import get_user_model
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.tokens import RefreshToken, TokenError
from datetime import datetime

User = get_user_model()

from organizations.models import Organization
from accounts.models import PasswordResetCode
from accounts.emails import send_password_reset_email
from organizations.utils import get_or_create_progress

class SPOSignupStartSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True, min_length=8)
    first_name = serializers.CharField(required=False, allow_blank=True)
    last_name = serializers.CharField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True)
    agree_to_terms = serializers.BooleanField()

    def validate(self, attrs):
        if attrs["password"] != attrs["confirm_password"]:
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})
        if User.objects.filter(email__iexact=attrs["email"]).exists():
            raise serializers.ValidationError({"email": "An account with this email already exists."})
        return attrs

    def create(self, data):
        email = data["email"].lower().strip()
        user = User.objects.create_user(
            email=email,
            password=data["password"],
            first_name=data.get("first_name", ""),
            last_name=data.get("last_name", ""),
            phone=data.get("phone", ""),
            role=User.Role.SPO,
        )
        user.terms_accepted = True
        user.terms_accepted_at = datetime.now()
        user.save(update_fields=["terms_accepted", "terms_accepted_at"])
        return user
    
class SPOProfileCompleteSerializer(serializers.Serializer):
    org_name = serializers.CharField()
    registration_type = serializers.ChoiceField(choices=Organization.RegistrationType.choices)
    date_of_incorporation = serializers.DateField(required=False, allow_null=True)
    gst_number = serializers.CharField(required=False, allow_blank=True)
    cin_number = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        user = self.context["request"].user
        if hasattr(user, "organization"):
            raise serializers.ValidationError({"organization": "Profile already completed for this user."})
        return attrs

    def create(self, data):
        user = self.context["request"].user
        return Organization.objects.create(
            name=data["org_name"],
            registration_type=data["registration_type"],
            date_of_incorporation=data.get("date_of_incorporation"),
            gst_number=data.get("gst_number", ""),
            cin_number=data.get("cin_number", ""),
            created_by=user,
        )
    
class EmailTokenObtainPairSerializer(TokenObtainPairSerializer):
    """
    SimpleJWT expects the username field; since USERNAME_FIELD = 'email',
    we can keep the default behavior but enrich the response.
    """
    @classmethod
    def get_token(cls, user):
        return super().get_token(user)

    def validate(self, attrs):
        # attrs keys: "email" (since USERNAME_FIELD), "password"
        data = super().validate(attrs)

        user: User = self.user
        prog = get_or_create_progress(user)
        # enrich response
        data["user"] = {
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "role": user.role
        }
        data["has_completed_profile"] = bool(prog.is_complete)
        data["onboarding"] = {
            "current_step": prog.current_step,
            "is_complete": prog.is_complete,
        }
        return data
    
class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()

    def validate(self, attrs):
        self.token = attrs["refresh"]
        return attrs

    def save(self, **kwargs):
        try:
            token = RefreshToken(self.token)
            token.blacklist()  # mark as invalid
        except TokenError:
            self.fail("bad_token")

    default_error_messages = {
        "bad_token": "Token is invalid or expired."
    }

class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        try:
            self.user = User.objects.get(email__iexact=value)
        except User.DoesNotExist:
            self.user = None
        return value

    def save(self, **kwargs):
        if self.user:
            code_obj = PasswordResetCode.issue_for(self.user)
            send_password_reset_email(self.user, code_obj)


class VerifyCodeSerializer(serializers.Serializer):
    email = serializers.EmailField()
    code = serializers.CharField(max_length=6)

    def validate(self, attrs):
        try:
            user = User.objects.get(email__iexact=attrs["email"])
        except User.DoesNotExist:
            raise serializers.ValidationError({"email": "Invalid email."})

        code_obj = PasswordResetCode.objects.filter(user=user).order_by("-created_at").first()
        if not code_obj or not code_obj.is_valid(attrs["code"]):
            raise serializers.ValidationError({"code": "Invalid or expired code."})

        attrs["user"] = user
        return attrs


class ResetPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()
    code = serializers.CharField(max_length=6)
    new_password = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True, min_length=8)

    def validate(self, attrs):
        if attrs["new_password"] != attrs["confirm_password"]:
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})
        return attrs

    def save(self, **kwargs):
        try:
            user = User.objects.get(email__iexact=self.validated_data["email"])
        except User.DoesNotExist:
            raise serializers.ValidationError({"email": "Invalid email."})

        code_obj = PasswordResetCode.objects.filter(user=user).order_by("-created_at").first()
        if not code_obj or not code_obj.is_valid(self.validated_data["code"]):
            raise serializers.ValidationError({"code": "Invalid or expired code."})

        user.set_password(self.validated_data["new_password"])
        user.save()
        code_obj.delete()  # invalidate code
        return user