from rest_framework import serializers
from .models import Assessment, Answer
from questionnaires.models import Section, Question, AnswerOption, QuestionDimension
from questionnaires.logic import evaluate_rule

class AssessmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Assessment
        fields = ["id", "status", "started_at", "submitted_at", "cooldown_until", "progress", "scores"]


class SectionSerializer(serializers.ModelSerializer):
    progress = serializers.SerializerMethodField()

    class Meta:
        model = Section
        fields = ["code", "title", "order", "progress"]

    def get_progress(self, section):
        progress = self.context.get("progress_by_section", {})
        return progress.get(section.code, {"answered": 0, "required": 0})

class AnswerOptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AnswerOption
        fields = ["label", "value", "points"]


class DimensionSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuestionDimension
        fields = ["code", "label", "min", "max", "weight", "points_per_unit"]


class QuestionSerializer(serializers.ModelSerializer):
    options = serializers.SerializerMethodField()
    dimensions = serializers.SerializerMethodField()
    answer = serializers.SerializerMethodField()
    min = serializers.SerializerMethodField()
    max = serializers.SerializerMethodField()
    step = serializers.SerializerMethodField()
    is_control = serializers.SerializerMethodField()

    class Meta:
        model = Question
        fields = [
            "code", "text", "help_text", "type", "required", "weight",
            "options", "dimensions", "min", "max", "step", "answer", "is_control",
        ]

    def get_options(self, obj):
        if obj.type in ["SINGLE_CHOICE", "MULTI_CHOICE"]:
            return [{"label": o.label, "value": o.value, "points": str(o.points)} for o in obj.options.all()]
        return None
    
    def get_is_control(self, obj):
        control_set = self.context.get("control_set", set())
        return obj.code in control_set

    def get_dimensions(self, obj):
        if obj.type == "MULTI_SLIDER":
            return [
                {
                    "code": d.code,
                    "label": d.label,
                    "min": d.min_value,
                    "max": d.max_value,
                    "weight": str(d.weight),
                    "points_per_unit": str(d.points_per_unit),
                }
                for d in obj.dimensions.all()
            ]
        return None

    # ---- Computed bounds without changing models ----
    def get_min(self, obj):
        if obj.type == "SLIDER":
            return 0
        if obj.type == "RATING":
            return 1
        return None

    def get_max(self, obj):
        if obj.type == "SLIDER":
            return int(obj.max_score) if obj.max_score is not None else 10
        if obj.type == "RATING":
            return int(obj.max_score) if obj.max_score is not None else 5
        return None

    def get_step(self, obj):
        if obj.type in ["SLIDER", "RATING"]:
            return 1
        return None

    def get_answer(self, obj):
        answers_map = self.context.get("answers_map", {})
        return answers_map.get(obj.code)


class AnswerUpsertSerializer(serializers.Serializer):
    question = serializers.CharField()
    data = serializers.JSONField()