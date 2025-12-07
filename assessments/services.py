from questionnaires.logic import evaluate_rule, _load_section_rules, _normalize_to_100, _clamp_0_100
from questionnaires.models import Section, Question
from typing import Dict, Tuple
from questionnaires.utils import extract_q_refs

def build_answers_map(assessment):
    sector = assessment.organization.focus_sector
    answers = assessment.answers.select_related("question").filter(
        question__sector=sector
    )

    return {a.question.code: a.data for a in answers}

def visible_questions_for_section(assessment, section):
    answers_map = build_answers_map(assessment)
    qs = section.questions.prefetch_related("options", "dimensions", "conditions").order_by("order")
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

def compute_scores(assessment) -> Tuple[Dict, Dict]:
    """
    Returns (scores, per_question_breakdown)
    scores = {"sections": {"IMPACT": 7.3, ...}, "overall": 6.2}
    per_question_breakdown = {
       "IMPACT": [{"code":"IMP_Q1","points":10.0,"weight":1.0,"weighted":10.0}, ...],
       ...
    }
    Feedback is ignored in section/overall.

    Overall score now uses the same weighted + normalized logic
    that eligibility_check applies, so both stay consistent.
    """
    answers_map = build_answers_map(assessment)
    scores: Dict[str, Any] = {"sections": {}, "overall": 0.0}
    breakdown: Dict[str, Any] = {}
    total = 0.0
    count = 0

    # ---- existing per-section logic (unchanged) ----
    for sec in Section.objects.all().order_by("order"):
        visible = visible_questions_for_section(assessment, sec)
        if not visible:
            continue

        sec_points = 0.0
        q_count = 0
        breakdown[sec.code] = []

        for q in visible:
            ans = answers_map.get(q.code)
            raw = question_points(q, ans)
            weighted = raw * float(q.weight)
            sec_points += weighted
            q_count += 1
            breakdown[sec.code].append({
                "code": q.code,
                "points": round(raw, 2),
                "weight": float(q.weight),
                "weighted": round(weighted, 2),
            })

        # Ignore FEEDBACK in numeric scoring, as before
        if q_count > 0 and sec.code != "FEEDBACK":
            avg = sec_points / q_count
            scores["sections"][sec.code] = round(avg, 2)
            total += avg
            count += 1

    # ---- new overall calculation: mirror eligibility_check ----
    sec_scores: Dict[str, Decimal] = scores["sections"] or {}

    # If we have no section scores, keep overall = 0.0
    if not sec_scores:
        scores["overall"] = 0.0
        return scores, breakdown

    rules_by_code = _load_section_rules()
    total_weighted = Decimal("0")
    weights_sum = Decimal("0")

    for section in Section.objects.all().order_by("order"):
        code = section.code
        raw_score = sec_scores.get(code)

        # Only consider a section if it has a rule and a score
        if (code not in rules_by_code) or (raw_score is None):
            continue

        rule = rules_by_code[code]
        norm_score = _normalize_to_100(raw_score)
        w = Decimal(str(rule.weight or 0))
        min_t = Decimal(str(rule.min_threshold))
        max_t = Decimal(str(rule.max_threshold))

        # Special-case for RISK (lower is better), same as eligibility_check
        if code.upper() == "RISK":
            # keep RISK raw scale (usually 0–40) if within max_t
            if raw_score is not None and Decimal(str(raw_score)) <= max_t:
                norm_score = Decimal(str(raw_score))

        contrib = Decimal("0")
        if w > 0:
            contrib = (norm_score * w) / Decimal("100")  # weight as percentage
            total_weighted += contrib
            weights_sum += w

    if weights_sum == 0:
        # No rules/weights contributed – fallback to old simple average behaviour
        scores["overall"] = round(total / count, 2) if count else 0.0
    else:
        overall_score = _clamp_0_100(
            total_weighted / (weights_sum / Decimal("100"))
        )
        # keep as float with 2 decimal places for consistency with previous API
        scores["overall"] = float(overall_score.quantize(Decimal("0.01")))

    return scores, breakdown