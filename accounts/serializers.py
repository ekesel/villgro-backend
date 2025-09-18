from django.contrib.auth import get_user_model
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from datetime import datetime

from organizations.models import Organization

User = get_user_model()

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
        # enrich response
        data["user"] = {
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "role": user.role,
            "has_completed_profile": hasattr(user, "organization"),
        }
        return data