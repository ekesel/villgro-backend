from rest_framework import serializers
from questionnaires.models import LoanRequest, FundType
from assessments.models import Assessment

class LoanMetaSerializer(serializers.Serializer):
    fund_types = serializers.ListField(child=serializers.DictField())

class LoanPrefillSerializer(serializers.Serializer):
    assessment_id = serializers.IntegerField()
    organization  = serializers.DictField()
    assessment_scores = serializers.DictField()  # {sections:..., overall:...}

class LoanRequestCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = LoanRequest
        fields = [
            "id", "assessment", "organization", "applicant",
            "founder_name", "founder_email", "amount_in_inr", "fund_type",
        ]
        read_only_fields = ["id", "organization", "applicant"]

    def validate(self, attrs):
        assessment: Assessment = attrs["assessment"]
        request = self.context["request"]
        user = request.user

        # SPO can only create for own org & latest assessment
        if assessment.organization.created_by_id != user.id and assessment.organization not in user.organizations.all():
            raise serializers.ValidationError("Not allowed for this organization/assessment.")

        # Ensure assessment is SUBMITTED and has scores
        if assessment.status != "SUBMITTED" or not assessment.scores or "sections" not in assessment.scores:
            raise serializers.ValidationError("Assessment must be submitted with scores before loan request.")

        return attrs

class LoanRequestDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = LoanRequest
        fields = [
            "id", "assessment", "organization", "applicant",
            "founder_name", "founder_email", "amount_in_inr", "fund_type",
            "eligibility_overall", "eligibility_decision", "eligibility_details",
            "status", "submitted_at", "created_at", "updated_at",
        ]
        read_only_fields = fields  # everything read-only for detail output

def fund_types_meta():
    return [{"value": ft.value, "label": ft.label} for ft in FundType]