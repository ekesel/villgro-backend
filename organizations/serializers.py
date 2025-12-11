from rest_framework import serializers
from organizations.models import OnboardingProgress, Organization
from questionnaires.models import Question

class OnboardingProgressSerializer(serializers.ModelSerializer):
    sectors = serializers.SerializerMethodField()

    class Meta:
        model = OnboardingProgress
        fields = ["current_step", "data", "is_complete", "updated_at", "sectors"]
        read_only_fields = ["is_complete", "updated_at"]

    def get_sectors(self, obj: OnboardingProgress):
        """
        Returns a distinct list of sections in the format:
        [
            {"label": "Impact Assessment", "value": "IMPACT"},
            {"label": "Risk Assessment", "value": "RISK"},
            ...
        ]
        """
        sectors = Question.objects \
            .values_list("sector", flat=True) \
            .distinct()
        
        sectors = set(sectors)

        return [
            {"label": sec, "value": sec}
            for sec in sectors if sec
        ]

class OnboardingProgressSaveSerializer(serializers.Serializer):
    # Save/merge partial data and/or move the pointer
    current_step = serializers.IntegerField(min_value=1, required=False)
    data = serializers.DictField(required=False)

    def update(self, instance: OnboardingProgress, validated_data):
        # merge data shallowly; FE can send full or partial keys
        new_data = validated_data.get("data")
        if new_data:
            merged = {**(instance.data or {}), **new_data}
            instance.data = merged
        if "current_step" in validated_data:
            step = validated_data["current_step"]
            instance.bump_to(step)
        instance.save()
        return instance

class OnboardingAdvanceSerializer(serializers.Serializer):
    # Optional: explicitly advance to a given step
    to_step = serializers.IntegerField(min_value=1, max_value=3)

class Step2Serializer(serializers.Serializer):
    type_of_innovation = serializers.ChoiceField(choices=Organization.InnovationType.choices)
    geo_scope = serializers.ChoiceField(choices=Organization.GeoScope.choices)
    top_states = serializers.ListField(
        child=serializers.CharField(max_length=64),
        allow_empty=True, required=False
    )

    def validate_top_states(self, value):
        if len(value) > 5:
            raise serializers.ValidationError("Select up to 5 states.")
        # Optional: ensure unique, strip blanks
        cleaned = [s.strip() for s in value if s.strip()]
        if len(set(cleaned)) != len(cleaned):
            raise serializers.ValidationError("States must be unique.")
        return cleaned

    def save(self, **kwargs):
        org: Organization = self.context["organization"]
        org.type_of_innovation = self.validated_data["type_of_innovation"]
        org.geo_scope = self.validated_data["geo_scope"]
        org.top_states = self.validated_data.get("top_states", [])
        org.save(update_fields=["type_of_innovation", "geo_scope", "top_states"])
        # progress jump to step 3
        prog: OnboardingProgress = self.context["progress"]
        if prog.current_step < 3:
            prog.current_step = 3
            prog.save(update_fields=["current_step", "updated_at"])
        return org


class Step3Serializer(serializers.Serializer):
    focus_sector = serializers.ChoiceField(choices=Organization.FocusSector.choices)
    org_stage = serializers.ChoiceField(choices=Organization.OrgStage.choices)
    impact_focus = serializers.ChoiceField(choices=Organization.ImpactFocus.choices)
    annual_operating_budget = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=0)
    use_of_questionnaire = serializers.ChoiceField(choices=Organization.UseOfQuestionnaire.choices)
    received_philanthropy_before = serializers.BooleanField()

    def save(self, **kwargs):
        org: Organization = self.context["organization"]
        for f in ["focus_sector","org_stage","impact_focus","annual_operating_budget","use_of_questionnaire","received_philanthropy_before"]:
            setattr(org, f, self.validated_data[f])
        org.save(update_fields=[
            "focus_sector","org_stage","impact_focus","annual_operating_budget",
            "use_of_questionnaire","received_philanthropy_before"
        ])
        return org


class FinishSerializer(serializers.Serializer):
    """No fields; we just validate all required org fields exist, then mark complete."""
    def validate(self, attrs):
        org: Organization = self.context["organization"]
        missing = []
        # Step 1 minimums
        if not org.name or not org.registration_type:
            missing += ["org_name/registration_type"]
        # Step 2
        if not org.type_of_innovation or not org.geo_scope:
            missing += ["type_of_innovation/geo_scope"]
        # Step 3
        for f in ["focus_sector","org_stage","impact_focus","annual_operating_budget","use_of_questionnaire","received_philanthropy_before"]:
            if getattr(org, f) in (None, "", []):
                missing.append(f)
        if missing:
            raise serializers.ValidationError({"missing": missing})
        return attrs

    def save(self, **kwargs):
        prog: OnboardingProgress = self.context["progress"]
        prog.is_complete = True
        prog.current_step = max(prog.current_step, 3)
        prog.save(update_fields=["is_complete","current_step","updated_at"])
        return prog