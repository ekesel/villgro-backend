from rest_framework import serializers
from django.db import transaction
from django.contrib.auth import get_user_model
from questionnaires.models import (
    Section, Question, AnswerOption, QuestionDimension, BranchingCondition
)
from banks.models import Bank
from organizations.models import Organization

User = get_user_model()

# -------- Sections
class SectionAdminSerializer(serializers.ModelSerializer):
    class Meta:
        model = Section
        fields = ["id", "code", "title", "order"]


# -------- Nested children
class AnswerOptionAdminSerializer(serializers.ModelSerializer):
    class Meta:
        model = AnswerOption
        fields = ["id", "label", "value", "points"]


class QuestionDimensionAdminSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuestionDimension
        fields = ["id", "code", "label", "min_value", "max_value", "points_per_unit", "weight"]


class BranchingConditionAdminSerializer(serializers.ModelSerializer):
    class Meta:
        model = BranchingCondition
        fields = ["id", "logic"]  # {"if":[{"==":["QCODE","YES"]}], "then": true}


# -------- Questions (with nested create/update + validations)
class QuestionAdminSerializer(serializers.ModelSerializer):
    options = AnswerOptionAdminSerializer(many=True, required=False)
    dimensions = QuestionDimensionAdminSerializer(many=True, required=False)
    conditions = BranchingConditionAdminSerializer(many=True, required=False)

    class Meta:
        model = Question
        fields = [
            "id", "section", "code", "text", "help_text",
            "type", "required", "order", "max_score", "weight",
            "options", "dimensions", "conditions", "is_active",
        ]

    # Type-specific validations that match the runtime engine
    def validate(self, attrs):
        instance = getattr(self, "instance", None)
        q_type = attrs.get("type", getattr(instance, "type", None))
        code = attrs.get("code", getattr(instance, "code", None))

        options = self.initial_data.get("options", None)
        dimensions = self.initial_data.get("dimensions", None)
        conditions = self.initial_data.get("conditions", None)

        # Code uniqueness
        if code:
            qs = Question.objects.filter(code=code)
            if instance:
                qs = qs.exclude(pk=instance.pk)
            if qs.exists():
                raise serializers.ValidationError({"code": "Question code must be unique."})

        # Choice requirements
        if q_type in ["SINGLE_CHOICE", "MULTI_CHOICE", "NPS"]:
            if options is None and instance is None:
                raise serializers.ValidationError({"options": "Choice types require options."})
            if options:
                for o in options:
                    if not o.get("value"):
                        raise serializers.ValidationError({"options": "Each option needs non-empty 'value'."})
                    if "points" not in o:
                        raise serializers.ValidationError({"options": "Each option needs 'points'."})

        # Slider requirements
        if q_type == "SLIDER":
            if dimensions is None and instance is None:
                raise serializers.ValidationError({"dimensions": "SLIDER requires exactly one dimension."})
            if dimensions:
                if len(dimensions) != 1:
                    raise serializers.ValidationError({"dimensions": "SLIDER must have exactly one dimension."})
                d = dimensions[0]
                if d.get("min_value") is None or d.get("max_value") is None:
                    raise serializers.ValidationError({"dimensions": "Dimension must have min_value and max_value."})

        # Multi-slider requirements
        if q_type == "MULTI_SLIDER":
            if dimensions is None and instance is None:
                raise serializers.ValidationError({"dimensions": "MULTI_SLIDER needs at least one dimension."})
            if dimensions:
                for d in dimensions:
                    if d.get("min_value") is None or d.get("max_value") is None:
                        raise serializers.ValidationError({"dimensions": "All dimensions need min/max."})

        # RATING: optional – allow as-is (value checked at runtime)

        # Validate branching conditions structure & references
        if conditions:
            # map question code -> option values for validation
            opt_map = {q.code: set(q.options.values_list("value", flat=True))
                       for q in Question.objects.prefetch_related("options")}
            # include pending code so you can add a condition that references itself in THEN true
            if code: opt_map.setdefault(code, set())

            for c in conditions:
                logic = c.get("logic") or {}
                if "if" not in logic or "then" not in logic:
                    raise serializers.ValidationError({"conditions": "Each condition.logic must have 'if' and 'then'."})

                preds = logic["if"]
                if not isinstance(preds, list):
                    raise serializers.ValidationError({"conditions": "'if' must be a list."})

                for p in preds:
                    if "==" not in p or not isinstance(p["=="], list) or len(p["=="]) != 2:
                        raise serializers.ValidationError({"conditions": "Only '==' predicate with 2 operands supported."})
                    ref_code, ref_val = p["=="]
                    if not Question.objects.filter(code=ref_code).exists():
                        raise serializers.ValidationError({"conditions": f"Unknown question code in condition: {ref_code}"})
                    # if reference is a choice question (has options), enforce valid option value
                    if ref_code in opt_map and opt_map[ref_code]:
                        if ref_val not in opt_map[ref_code]:
                            raise serializers.ValidationError({"conditions": f"Invalid option '{ref_val}' for {ref_code}"})

                # 'then' is usually boolean (true = show), we accept truthy
                # (your engine treats presence of satisfied condition as visibility=true)
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        opts = validated_data.pop("options", [])
        dims = validated_data.pop("dimensions", [])
        conds = validated_data.pop("conditions", [])
        q = Question.objects.create(**validated_data)
        for o in opts: AnswerOption.objects.create(question=q, **o)
        for d in dims: QuestionDimension.objects.create(question=q, **d)
        for c in conds: BranchingCondition.objects.create(question=q, **c)
        return q

    @transaction.atomic
    def update(self, instance, validated_data):
        opts = validated_data.pop("options", None)
        dims = validated_data.pop("dimensions", None)
        conds = validated_data.pop("conditions", None)

        for k, v in validated_data.items():
            setattr(instance, k, v)
        instance.save()

        def replace(qs, data, Model):
            qs.all().delete()
            for item in data:
                Model.objects.create(question=instance, **item)

        if opts is not None: replace(instance.options, opts, AnswerOption)
        if dims is not None: replace(instance.dimensions, dims, QuestionDimension)
        if conds is not None: replace(instance.conditions, conds, BranchingCondition)
        return instance
    
class BankAdminSerializer(serializers.ModelSerializer):
    class Meta:
        model = Bank
        fields = [
            "id", "name", "contact_person", "contact_email",
            "contact_phone", "status", "notes",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

class AdminSPOOrgSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = [
            "id", "name", "registration_type",
            "date_of_incorporation", "gst_number", "cin_number",
            "type_of_innovation", "geo_scope", "top_states",
            "focus_sector", "org_stage", "impact_focus",
            "annual_operating_budget", "use_of_questionnaire",
            "received_philanthropy_before",
        ]
        read_only_fields = ["id"]

class AdminSPOListSerializer(serializers.ModelSerializer):
    organization = AdminSPOOrgSerializer(read_only=True)

    class Meta:
        model = User
        fields = [
            "id", "email", "first_name", "last_name", "phone",
            "is_active", "date_joined", "organization",
        ]

class AdminSPOCreateSerializer(serializers.Serializer):
    # user fields
    email = serializers.EmailField()
    first_name = serializers.CharField(required=False, allow_blank=True)
    last_name  = serializers.CharField(required=False, allow_blank=True)
    phone      = serializers.CharField(required=False, allow_blank=True)
    password   = serializers.CharField(write_only=True)

    # org minimal
    organization = serializers.DictField(child=serializers.JSONField(), write_only=True)

    def validate(self, data):
        org = data.get("organization") or {}
        if "name" not in org or "registration_type" not in org:
            raise serializers.ValidationError("organization.name and organization.registration_type are required")
        return data

    def create(self, validated):
        org_payload = validated.pop("organization")
        password = validated.pop("password")

        user = User.objects.create_user(
            role=User.Role.SPO, **validated
        )
        user.set_password(password)
        user.save()

        Organization.objects.create(
            created_by=user,
            name=org_payload["name"],
            registration_type=org_payload["registration_type"],
            date_of_incorporation=org_payload.get("date_of_incorporation"),
            gst_number=org_payload.get("gst_number",""),
            cin_number=org_payload.get("cin_number",""),
        )
        return user

class AdminSPOUpdateSerializer(serializers.ModelSerializer):
    # allow updating some org fields inline
    organization = AdminSPOOrgSerializer(required=False)

    class Meta:
        model = User
        fields = ["first_name", "last_name", "phone", "is_active", "organization"]

    def update(self, instance, validated):
        org_data = validated.pop("organization", None)
        for k, v in validated.items():
            setattr(instance, k, v)
        instance.save()

        if org_data:
            org = instance.organization
            for k, v in org_data.items():
                setattr(org, k, v)
            org.save()
        return instance