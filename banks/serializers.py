# banks/serializers.py
from rest_framework import serializers

class BankSPOListItemSerializer(serializers.Serializer):
    """
    Row for SPOs Management table
    """
    id = serializers.IntegerField(help_text="SPO User ID")
    email = serializers.EmailField()
    first_name = serializers.CharField(allow_blank=True)
    last_name = serializers.CharField(allow_blank=True)
    is_active = serializers.BooleanField()
    date_joined = serializers.DateTimeField()

    organization_name = serializers.CharField(allow_blank=True)
    focus_sector = serializers.CharField(allow_blank=True, help_text="Organization.focus_area exposed as focus_sector")
    # dates summary
    org_created_at = serializers.DateTimeField(allow_null=True)
    last_assessment_submitted_at = serializers.DateTimeField(allow_null=True)
    last_loan_request_submitted_at = serializers.DateTimeField(allow_null=True)
    instrument = serializers.JSONField(read_only=True, allow_null=True)
    scores = serializers.JSONField(read_only=True, allow_null=True)


class BankSPODetailAssessmentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    status = serializers.CharField()
    started_at = serializers.DateTimeField(allow_null=True)
    submitted_at = serializers.DateTimeField(allow_null=True)
    scores = serializers.JSONField()

    # merged eligibility (if exists)
    eligibility_overall = serializers.FloatField(allow_null=True)
    eligibility_decision = serializers.BooleanField(allow_null=True)
    eligibility_reason = serializers.CharField(allow_blank=True, allow_null=True)

    # linked latest loan request (if any)
    loan_request_id = serializers.IntegerField(allow_null=True)


class BankSPODetailSerializer(serializers.Serializer):
    """
    Detail page summary
    """
    spo = serializers.DictField(help_text="SPO user header")
    organization = serializers.DictField(help_text="Organization info from SPO’s org")
    assessments = BankSPODetailAssessmentSerializer(many=True)
    email_placeholder = serializers.CharField(help_text="Frontend placeholder for email flow", allow_blank=True)