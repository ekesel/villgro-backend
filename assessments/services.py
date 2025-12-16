from questionnaires.logic import evaluate_rule, _load_section_rules, _normalize_to_100, _clamp_0_100
from questionnaires.models import Section, Question
from typing import Dict, Tuple, Any
from questionnaires.utils import extract_q_refs

def build_answers_map(assessment):
    sector = assessment.organization.focus_sector
    answers = assessment.answers.select_related("question").filter(
        question__sector=sector
    )

    return {a.question.code: a.data for a in answers}

def visible_questions_for_section(assessment, section):
    answers_map = build_answers_map(assessment)
    qs = section.questions.filter(sector=assessment.organization.focus_sector).prefetch_related("options", "dimensions", "conditions").order_by("order")
    visible = []
    for q in qs:
        conds = list(q.conditions.all())
        if not conds or any(evaluate_rule(c.logic, answers_map) for c in conds):
            visible.append(q)
    return visible

def compute_progress(assessment):
    answers_map = build_answers_map(assessment)
    progress = {"answered": 0, "required": 0, "by_section": {}}

    for sec in Section.objects.all().order_by("order"):
        vis_qs = visible_questions_for_section(assessment, sec)
        answered = 0
        required = 0
        for q in vis_qs:
            if q.required:
                required += 1
            if answers_map.get(q.code):
                answered += 1
        progress["by_section"][sec.code] = {"answered": answered, "required": required}
        progress["answered"] += answered
        progress["required"] += required

    total_req = progress["required"]
    progress["percent"] = int(round((progress["answered"] / total_req) * 100)) if total_req else 0

    if isinstance(getattr(assessment, "progress", {}), dict):
        if "last_section" in assessment.progress and "last_section" not in progress:
            progress["last_section"] = assessment.progress.get("last_section")

    return progress

def get_control_qcodes() -> set:
    control = set()
    for q in Question.objects.filter(is_active=True).prefetch_related("conditions").all():
        for cond in q.conditions.all():
            control |= extract_q_refs(cond.logic)
    return control

def question_points(q: Question, ans: dict) -> float:
    """Compute raw points for a single question from its answer payload."""
    if not ans:
        return 0.0

    if q.type in ["SINGLE_CHOICE", "NPS"]:
        val = ans.get("value")
        opt = q.options.filter(value=val).first()
        return float(opt.points) if opt else 0.0

    if q.type == "MULTI_CHOICE":
        vals = set(ans.get("values", []))
        pts = 0.0
        for opt in q.options.all():
            if opt.value in vals:
                pts += float(opt.points)
        return pts

    if q.type in ["SLIDER", "RATING"]:
        val = ans.get("value")
        return float(val) if val is not None else 0.0

    if q.type == "MULTI_SLIDER":
        vals = ans.get("values", {}) or {}
        pts = 0.0
        for d in q.dimensions.all():
            if d.code in vals:
                pts += float(vals[d.code]) * float(d.points_per_unit) * float(d.weight)
        return pts

    return 0.0

from decimal import Decimal

def _safe_float(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)


def _question_max_points(q: Question) -> float:
    """
    Max achievable points for the question (raw points, before question.weight).
    This is needed to normalize section scores to 0..100 by max-possible.
    """
    if q.type in ["SINGLE_CHOICE", "NPS"]:
        opts = list(q.options.all())
        return max((_safe_float(o.points) for o in opts), default=0.0)

    if q.type in ["MULTI_CHOICE"]:
        # Assumption: points are non-negative; selecting more options increases points.
        return sum(max(_safe_float(o.points), 0.0) for o in q.options.all())

    if q.type in ["SLIDER", "RATING"]:
        return _safe_float(getattr(q, "max_score", 0) or 0)

    if q.type in ["MULTI_SLIDER"]:
        # We need a max per dimension. Try common field names; fallback to 10.
        dims = list(q.dimensions.all())
        max_total = 0.0
        for d in dims:
            dim_max = (
                getattr(d, "max_value", None)
                or getattr(d, "max", None)
                or getattr(d, "upper_bound", None)
                or 10
            )
            max_total += _safe_float(dim_max) * _safe_float(d.points_per_unit) * _safe_float(d.weight)
        return max_total

    return 0.0


def compute_scores(assessment) -> Tuple[Dict, Dict]:
    """
    Returns (scores, per_question_breakdown)

    Section scores returned on a TRUE 0..100 scale:
        section_score = (sum(earned * q.weight) / sum(max_possible * q.weight)) * 100

    RISK remains "lower is better" naturally (score is risk level on 0..100);
       eligibility_check uses max_threshold to enforce low risk.
    """
    answers_map = build_answers_map(assessment)
    scores: Dict[str, Any] = {"sections": {}, "overall": 0.0}
    breakdown: Dict[str, Any] = {}

    # ---- section scores (0..100 by max achievable) ----
    for sec in Section.objects.all().order_by("order"):
        if sec.code == "FEEDBACK":
            continue

        visible = visible_questions_for_section(assessment, sec)
        if not visible:
            continue

        earned_weighted = 0.0
        max_weighted = 0.0
        breakdown[sec.code] = []

        for q in visible:
            ans = answers_map.get(q.code)
            if not ans:
                continue  # unanswered doesn't contribute

            w = _safe_float(q.weight or 0)
            raw_pts = _safe_float(question_points(q, ans))
            max_pts = _safe_float(_question_max_points(q))

            earned_weighted += raw_pts * w
            max_weighted += max_pts * w

            breakdown[sec.code].append({
                "code": q.code,
                "points": round(raw_pts, 2),
                "max": round(max_pts, 2),
                "weight": round(w, 2),
                "weighted": round(raw_pts * w, 2),
                "weighted_max": round(max_pts * w, 2),
            })

        if max_weighted > 0:
            norm_0_100 = _clamp_0_100((Decimal(str(earned_weighted)) / Decimal(str(max_weighted))) * Decimal("100"))
            scores["sections"][sec.code] = float(norm_0_100.quantize(Decimal("0.01")))
        else:
            scores["sections"][sec.code] = 0.0

    # ---- overall (doc style: weighted sum on 0..100) ----
    rules_by_code = _load_section_rules()
    total_weighted = Decimal("0")
    weights_sum = Decimal("0")

    for code, sec_score in (scores.get("sections") or {}).items():
        rule = rules_by_code.get(code)
        if not rule:
            continue

        w = Decimal(str(rule.weight or 0))
        if w <= 0:
            continue

        sec_score_dec = Decimal(str(sec_score))  # already 0..100
        total_weighted += (sec_score_dec * w) / Decimal("100")
        weights_sum += w

    if weights_sum <= 0:
        scores["overall"] = 0.0
    else:
        overall = _clamp_0_100(total_weighted / (weights_sum / Decimal("100")))
        scores["overall"] = float(overall.quantize(Decimal("0.01")))

    return scores, breakdown