from rest_framework import serializers
from .models import Assessment
from questionnaires.models import Section, Question, AnswerOption, QuestionDimension

GRAPH_RANGES = {
    "RISK":   {"min": -20.0, "max": 100.0},
    "IMPACT": {"min": 0.0,   "max": 2000.0},
    "RETURN": {"min": 0.0,   "max": 100.0},
}

def _normalize(value: float | int | None, lo: float, hi: float) -> float | None:
    """Map value to 0–100; clamp outside. Returns None if value is None."""
    if value is None:
        return None
    if hi <= lo:
        return 0.0
    scaled = 100.0 * (float(value) - lo) / (hi - lo)
    return max(0.0, min(100.0, scaled))


class AssessmentSerializer(serializers.ModelSerializer):
    graph = serializers.SerializerMethodField()

    class Meta:
        model = Assessment
        fields = [
            "id", "status", "started_at", "submitted_at",
            "cooldown_until", "progress", "scores", "graph",
        ]

    def get_graph(self, obj: Assessment) -> dict:
        """
        Normalized Risk–Return–Impact graph payload for frontend visualization.
        Combines old normalization logic with the new frontend schema.
        """
        scores = obj.scores or {}
        sections = scores.get("sections") or {}

        raw_risk = sections.get("RISK")
        raw_impact = sections.get("IMPACT")
        raw_return = sections.get("RETURN")
        overall = scores.get("overall")

        # Preserve normalization logic
        r = GRAPH_RANGES
        norm_risk = _normalize(raw_risk, r["RISK"]["min"], r["RISK"]["max"])
        norm_impact = _normalize(raw_impact, r["IMPACT"]["min"], r["IMPACT"]["max"])
        norm_return = _normalize(raw_return, r["RETURN"]["min"], r["RETURN"]["max"])

        # Maintain existing section-level data, add normalized versions if needed
        normalized_sections = {
            "RISK": norm_risk,
            "IMPACT": norm_impact,
            "RETURN": norm_return,
            **{k: v for k, v in sections.items() if k not in ["RISK", "IMPACT", "RETURN"]},
        }

        return {
            "scores": {
                "overall": overall,
                "sections": normalized_sections,
            },
            "plot": {
                "x": "RISK",
                "y": "IMPACT",
                "z": "RETURN",
            },
        }

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
        return [{"label": o.label, "value": o.value, "points": str(o.points)} for o in obj.options.all()]
    
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