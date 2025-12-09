from rest_framework import serializers
from django.db import transaction
from accounts.models import User
from questionnaires.models import (
    Section, Question, AnswerOption, QuestionDimension, BranchingCondition
)
import re
from banks.models import Bank
from organizations.models import Organization
from django.db.models import Sum
import time
from admin_portal.models import ActivityLog
from django.contrib.auth.password_validation import validate_password


# -------- Sections
class SectionAdminSerializer(serializers.ModelSerializer):
    total_questions = serializers.SerializerMethodField()
    active_questions = serializers.SerializerMethodField()
    inactive_questions = serializers.SerializerMethodField()
    weightage = serializers.SerializerMethodField()

    class Meta:
        model = Section
        fields = [
            "id", "code", "title", "order",
            "weightage", "total_questions",
            "active_questions", "inactive_questions",
        ]

    def get_total_questions(self, obj):
        context = self.context or {}
        sector = context.get("sector", None)
        return Question.objects.filter(section=obj, sector=sector).count()

    def get_active_questions(self, obj):
        context = self.context or {}
        sector = context.get("sector", None)
        return Question.objects.filter(section=obj, is_active=True, sector=sector).count()

    def get_inactive_questions(self, obj):
        context = self.context or {}
        sector = context.get("sector", None)
        return Question.objects.filter(section=obj, is_active=False, sector=sector).count()

    def get_weightage(self, obj):
        """Calculate weightage as percentage of section’s total weight vs all sections."""
        context = self.context or {}
        sector = context.get("sector", None)
        qs = Question.objects.filter(is_active=True, sector=sector)
        total_weight = qs.aggregate(total=Sum("weight"))["total"] or 0
        section_weight = qs.filter(section=obj).aggregate(total=Sum("weight"))["total"] or 0
        if total_weight == 0:
            return 0
        return round((section_weight / total_weight) * 100, 2)


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
            "options", "dimensions", "conditions", "is_active", "sector",
        ]

    # ---- helpers ----------------------------------------------------------
    def _autogenerate_code(self, attrs):
        """Generate a stable-ish code if not supplied."""
        if attrs.get("code"):
            return
        
        instance = getattr(self, "instance", None)

        if instance is not None and getattr(instance, "code", None):
            return
        
        sec = attrs.get("section") or (getattr(instance, "section", None) if instance else None)
        sec_code = getattr(sec, "code", None) or "Q"
        try:
            # section may be a PK or object
            sec_code = getattr(sec, "code", None) or str(sec)
        except Exception:
            sec_code = "Q"
        # Use epoch seconds to avoid collisions during admin usage
        attrs["code"] = f"{sec_code}_Q{int(time.time())}"

    def _normalize_condition_logic(self, logic: dict) -> dict:
        """
        Accepts either canonical {"if":[{"==":[code,val]}], "then": true}
        or simplified {"q": code, "op": "", "val": value}.
        Defaults op to 'eq' when missing/blank.
        """
        if "if" in logic and "then" in logic:
            return logic  # already canonical

        q = logic.get("q")
        op = (logic.get("op") or "").strip().lower() or "eq"
        val = logic.get("val")

        # For now we only map 'eq' → '=='. You can extend this if needed.
        if op != "eq":
            # You can raise an error or coerce unsupported ops as needed:
            # raise serializers.ValidationError({"conditions": f"Unsupported op '{op}' (only 'eq' is supported)."})
            op_symbol = "=="  # graceful fallback to 'eq'
        else:
            op_symbol = "=="

        return {"if": [{op_symbol: [q, val]}], "then": True}

    # ---- validation -------------------------------------------------------

    def validate(self, attrs):
        instance = getattr(self, "instance", None)
        q_type = attrs.get("type", getattr(instance, "type", None))

        # Autogenerate code if omitted
        self._autogenerate_code(attrs)
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

        # Normalize and lightly validate branching
        if conditions:
            # Build map: question_code -> set(option_values) for value validation where possible
            opt_map = {q.code: set(q.options.values_list("value", flat=True))
                       for q in Question.objects.prefetch_related("options")}
            # ensure current code present (even if new)
            if code:
                opt_map.setdefault(code, set())

            normalized_conds = []
            for c in conditions:
                logic = c.get("logic") or {}
                logic = self._normalize_condition_logic(logic)
                # lightweight structure check
                if "if" not in logic or "then" not in logic or not isinstance(logic["if"], list):
                    raise serializers.ValidationError({"conditions": "Each condition.logic must normalize to {'if': [...], 'then': ...}."})

                # validate ONLY what we can know now; allow forward refs
                preds = logic["if"]
                for p in preds:
                    # Only '==' supported currently
                    if "==" not in p or not isinstance(p["=="], list) or len(p["=="]) != 2:
                        raise serializers.ValidationError({"conditions": "Only 'eq' (→ '==') predicate with 2 operands is supported."})
                    # ref_code, ref_val = p["=="]

                    # # Do not hard-fail if ref question doesn't exist yet (admin may add it next).
                    # # If it exists and has options, ensure value is valid.
                    # if ref_code in opt_map and opt_map[ref_code]:
                    #     if ref_val not in opt_map[ref_code]:
                    #         raise serializers.ValidationError({"conditions": f"Invalid option '{ref_val}' for {ref_code}"})

                normalized_conds.append({"logic": logic})

            # replace with normalized list so create/update uses canonical form
            self._normalized_conditions = normalized_conds
        else:
            self._normalized_conditions = None

        return attrs

    # ---- persistence ------------------------------------------------------
    @transaction.atomic
    def create(self, validated_data):
        opts = validated_data.pop("options", [])
        dims = validated_data.pop("dimensions", [])
        # use normalized conditions if present
        conds = self._normalized_conditions if hasattr(self, "_normalized_conditions") else validated_data.pop("conditions", [])
        q = Question.objects.create(**validated_data)
        for o in (opts or []):
            AnswerOption.objects.create(question=q, **o)
        for d in (dims or []):
            QuestionDimension.objects.create(question=q, **d)
        for c in (conds or []):
            BranchingCondition.objects.create(question=q, **c)
        return q

    @transaction.atomic
    def update(self, instance, validated_data):
        opts = validated_data.pop("options", None)
        dims = validated_data.pop("dimensions", None)
        conds_in = self._normalized_conditions if hasattr(self, "_normalized_conditions") else validated_data.pop("conditions", None)

        for k, v in validated_data.items():
            if k in ["options", "dimensions", "conditions"]:
                continue
            setattr(instance, k, v)
        instance.save()

        def replace(qs, data, Model):
            qs.all().delete()
            for item in data:
                Model.objects.create(question=instance, **item)

        if opts is not None:
            replace(instance.options, opts, AnswerOption)
        if dims is not None:
            replace(instance.dimensions, dims, QuestionDimension)
        if conds_in is not None:
            replace(instance.conditions, conds_in, BranchingCondition)
        return instance
    
class BankAdminSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=True)
    class Meta:
        model = Bank
        fields = [
            "id", "name", "contact_person", "contact_email",
            "contact_phone", "status", "notes",
            "created_at", "updated_at", "password",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    
    def validate(self, attrs):
        # On create, password is mandatory
        if self.instance is None and not attrs.get("password"):
            raise serializers.ValidationError({
                "password": "This field is required."
            })
        return super().validate(attrs)

    def validate_contact_phone(self, value):
        """
            E.164 format: +<country_code><number>
            Example: +14155552671
        """
        pattern = r'^\+\d{7,15}$'
        if not re.match(pattern, value):
            raise serializers.ValidationError("Enter a valid phone number in E.164 format (e.g., +14155552671).")
        return value

    def validate_contact_email(self, v):
        qs = User.objects.filter(email__iexact=v)

        # If we're updating an existing Bank, allow its own BANK_USER's email
        bank = getattr(self, "instance", None)
        if bank and getattr(bank, "user_id", None):
            qs = qs.exclude(pk=bank.user_id)

        if qs.exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return v

    def validate_password(self, v):
        validate_password(v)
        return v

    def create(self, validated_data):
        # pop user fields
        user_email = validated_data.get("contact_email", "")
        user_password = validated_data.pop("password")
        user_first_name = validated_data.get("contact_person", "")
        user_phone = validated_data.get("contact_phone", "")

        # 1) create BANK_USER
        user = User.objects.create_user(
            email=user_email,
            password=user_password,
            role=User.Role.BANK_USER,
            first_name=user_first_name,
            phone=user_phone,
        )

        # 2) create Bank and link user
        bank = Bank.objects.create(user=user, **validated_data)
        return bank
    
    def update(self, instance, validated_data):
        """
        Update Bank + its linked BANK_USER.
        - If password is provided, update the User's password.
        - If contact_email/person/phone change, also update the User.
        """
        # Pop fields meant for User
        new_email = validated_data.get("contact_email", instance.contact_email)
        new_person = validated_data.get("contact_person", instance.contact_person)
        new_phone = validated_data.get("contact_phone", instance.contact_phone)
        new_password = validated_data.pop("password", None)

        user = getattr(instance, "user", None)

        # Sync to User if present
        if user:
            # Email
            if new_email and new_email != user.email:
                user.email = new_email

            # First name
            if new_person and new_person != user.first_name:
                user.first_name = new_person

            # Phone
            if new_phone and new_phone != user.phone:
                user.phone = new_phone

            # Password
            if new_password:
                user.set_password(new_password)

            user.save()

        # Normal Bank fields update (everything except password)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        return instance

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

class ActivityLogSerializer(serializers.ModelSerializer):
    actor_email = serializers.SerializerMethodField()

    class Meta:
        model = ActivityLog
        fields = [
            "id", "created_at", "actor", "actor_email",
            "action", "app_label", "model", "object_id", "object_repr",
            "changes", "meta", "help_text",
        ]

    def get_actor_email(self, obj):
        return getattr(obj.actor, "email", None)
    
class AdminReviewListSerializer(serializers.Serializer):
    """
    Flat payload for Admin Reviews list.
    """
    id = serializers.IntegerField(help_text="Feedback ID")
    assessment_id = serializers.IntegerField()
    date = serializers.DateTimeField(source="created_at")
    user_id = serializers.IntegerField()
    user_email = serializers.EmailField()
    organization_name = serializers.CharField()
    status = serializers.CharField(help_text="Completed/Incomplete derived from assessment.status")
    review = serializers.CharField(allow_blank=True)
    reasons = serializers.ListField(child=serializers.CharField())

class AdminReviewDetailSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    assessment_id = serializers.IntegerField()
    date = serializers.DateTimeField(source="created_at")
    user = serializers.DictField()
    organization = serializers.DictField()
    status = serializers.CharField()
    reasons = serializers.ListField(child=serializers.CharField())
    comment = serializers.CharField(allow_blank=True)

class AdminUserSerializerLite(serializers.Serializer):
    id = serializers.IntegerField()
    email = serializers.EmailField()
    first_name = serializers.CharField(allow_blank=True)
    last_name = serializers.CharField(allow_blank=True)
    is_active = serializers.BooleanField()
    date_joined = serializers.DateTimeField()

class AdminUserCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    first_name = serializers.CharField(required=False, allow_blank=True)
    last_name = serializers.CharField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True)
    password = serializers.CharField(write_only=True)

    def create(self, validated_data):
        u = User.objects.create_user(
            email=validated_data["email"],
            password=validated_data["password"],
            role=User.Role.ADMIN,
            first_name=validated_data.get("first_name",""),
            last_name=validated_data.get("last_name",""),
            phone=validated_data.get("phone",""),
            is_staff=True,
        )
        return u
    
    def validate_password(self, value):
        validate_password(value)
        return value
    
class AssessmentCooldownConfigSerializer(serializers.Serializer):
    days = serializers.IntegerField(
        min_value=0,
        help_text="Cooldown in days before a startup can begin a new assessment."
    )