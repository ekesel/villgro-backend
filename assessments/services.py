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

def compute_scores(assessment) -> Tuple[Dict, Dict]:
    """
    Returns (scores, per_question_breakdown)

    - Section scores are returned on a 0..100 scale.
    - RISK is also 0..100 where LOWER is better (risk level), NOT inverted.
    - Overall = sum(section_score * weight/100) for rule-defined sections.
    """
    answers_map = build_answers_map(assessment)
    scores: Dict[str, Any] = {"sections": {}, "overall": 0.0}
    breakdown: Dict[str, Any] = {}

    # ---- section scores ----
    for sec in Section.objects.all().order_by("order"):
        visible = visible_questions_for_section(assessment, sec)
        if not visible:
            continue

        sec_points = 0.0
        weight_sum = 0.0
        breakdown[sec.code] = []

        for q in visible:
            ans = answers_map.get(q.code)
            if not ans:
                continue  # unanswered doesn't contribute

            raw_pts = question_points(q, ans)
            w = float(q.weight or 0) or 0.0
            weighted = raw_pts * w

            sec_points += weighted
            weight_sum += w

            breakdown[sec.code].append({
                "code": q.code,
                "points": round(raw_pts, 2),
                "weight": w,
                "weighted": round(weighted, 2),
            })

        if weight_sum > 0 and sec.code != "FEEDBACK":
            # This gives a "raw section score" on whatever scale the questions are on.
            raw_avg = sec_points / weight_sum

            # Convert to 0..100 explicitly:
            # Assumption: raw_avg is on 0..10 scale for IMPACT/RISK/RETURN.
            # If your scale differs per section, we can map per section.
            norm_0_100 = _clamp_0_100(Decimal(str(raw_avg)) * Decimal("10"))

            scores["sections"][sec.code] = float(norm_0_100.quantize(Decimal("0.01")))

    # ---- overall ----
    rules_by_code = _load_section_rules()
    total_weighted = Decimal("0")
    weights_sum = Decimal("0")

    for section in Section.objects.all().order_by("order"):
        code = section.code
        if code not in rules_by_code:
            continue
        if code not in scores["sections"]:
            continue

        rule = rules_by_code[code]
        w = Decimal(str(rule.weight or 0))
        if w <= 0:
            continue

        sec_score = Decimal(str(scores["sections"][code]))  # already 0..100
        contrib = (sec_score * w) / Decimal("100")

        total_weighted += contrib
        weights_sum += w

    if weights_sum <= 0:
        scores["overall"] = 0.0
    else:
        # since scores are already 0..100 and w is %, this is already 0..100
        overall = _clamp_0_100(total_weighted / (weights_sum / Decimal("100")))
        scores["overall"] = float(overall.quantize(Decimal("0.01")))

    return scores, breakdown