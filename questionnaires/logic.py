# questionnaire/logic.py
from __future__ import annotations
from typing import Any, Dict
from decimal import Decimal
from django.db import transaction
from django.utils import timezone

from assessments.models import Assessment
from questionnaires.models import Section, EligibilityRule, LoanEligibilityResult, LoanInstrument

Rule = Dict[str, Any]
AnswersMap = Dict[str, Any]  # {"Q_CODE": {"value": "YES"} or {"values": [...]} or {"values": {"dim": 7}}}

def _get_answer_value(ans: Any):
    """
    Normalize stored answer shapes so ops work:
    - SINGLE_CHOICE / SLIDER / RATING: {"value": X} -> X
    - MULTI_CHOICE: {"values": [..]}   -> set([...])
    - MULTI_SLIDER: {"values": {"dim": n}} -> dict
    """
    if ans is None:
        return None
    if isinstance(ans, dict):
        if "value" in ans:
            return ans["value"]
        if "values" in ans:
            return ans["values"]
    return ans

def _op_eval(left, op: str, right):
    if op == "eq":   return left == right
    if op == "ne":   return left != right
    if op == "gt":   return left is not None and right is not None and left >  right
    if op == "gte":  return left is not None and right is not None and left >= right
    if op == "lt":   return left is not None and right is not None and left <  right
    if op == "lte":  return left is not None and right is not None and left <= right
    if op == "in":
        if isinstance(right, (list, tuple, set)):
            return left in right
        return False
    if op == "nin":
        if isinstance(right, (list, tuple, set)):
            return left not in right
        return False
    if op == "contains":
        # supports multi-select sets/lists and dicts (for multi-slider dims)
        if isinstance(left, dict):
            # right could be a key or {key: value} (exact)
            if isinstance(right, dict):
                return all(k in left and left[k] == v for k, v in right.items())
            return right in left
        if isinstance(left, (list, set, tuple)):
            return right in left
        if isinstance(left, str) and isinstance(right, str):
            return right in left
        return False
    return False

def evaluate_rule(rule: Rule, answers: AnswersMap) -> bool:
    """
    Evaluate rule JSON against current answers.

    Supported primitives:
      {"q": "IMP_Q1", "op": "eq", "val": "YES"}
      {"q": "RISK_Q2", "op": "in", "val": ["A","B"]}
      {"q": "RET_Q3",  "op": "gte", "val": 7}
      {"q": "IMP_Q4",  "op": "contains", "val": {"reach": 5}}

    Combinators:
      {"all": [ ...rules... ]}
      {"any": [ ...rules... ]}
      {"not": { ...rule... }}
    """
    if not rule:
        return True

    # combinators
    if "all" in rule:
        return all(evaluate_rule(r, answers) for r in rule["all"])
    if "any" in rule:
        return any(evaluate_rule(r, answers) for r in rule["any"])
    if "not" in rule:
        return not evaluate_rule(rule["not"], answers)

    # primitive
    q_code = rule.get("q")
    op = rule.get("op")
    val = rule.get("val")
    if not q_code or not op:
        return True  # be permissive

    raw = answers.get(q_code)
    left = _get_answer_value(raw)
    # normalize MULTI_CHOICE to set for in/contains convenience
    if isinstance(left, list):
        left = set(left)
    return _op_eval(left, op, val)


# Tunables
DEFAULT_OVERALL_PASS_THRESHOLD = Decimal("70.0")  # overall >= 70 to pass (after all section gates pass)
CLAMP_MIN = Decimal("0")
CLAMP_MAX = Decimal("100")


def _clamp_0_100(x: Decimal | float | int) -> Decimal:
    try:
        d = Decimal(str(x))
    except Exception:
        return Decimal("0")
    if d < CLAMP_MIN:
        return CLAMP_MIN
    if d > CLAMP_MAX:
        return CLAMP_MAX
    return d


def _normalize_to_100(raw: Decimal | float | int) -> Decimal:
    """
    Heuristic normalization to 0–100.
    - If 0..1  -> *100.
    - Else if 0..10 -> *10.
    - Else if 0..100 -> keep.
    Any outliers are clamped.
    """
    try:
        d = Decimal(str(raw))
    except Exception:
        return Decimal("0")

    # IMPORTANT: check smaller scales first
    if Decimal("0") <= d <= Decimal("1"):
        return _clamp_0_100(d * Decimal("100"))
    if Decimal("0") <= d <= Decimal("10"):
        return _clamp_0_100(d * Decimal("10"))
    if Decimal("0") <= d <= Decimal("100"):
        return _clamp_0_100(d)

    # If it's >100 (e.g., sums), cap it.
    return _clamp_0_100(d)


def _load_section_rules() -> Dict[str, EligibilityRule]:
    """
    Returns a mapping: section_code -> EligibilityRule
    Only active sections that actually have a rule seeded will matter.
    """
    rules: Dict[str, EligibilityRule] = {}
    for r in EligibilityRule.objects.select_related("section").all():
        rules[r.section.code] = r
    return rules


def _pick_instrument(
    overall_score: Decimal,
    details: Dict,
    *,
    stage: str | None = None,
) -> LoanInstrument | None:
    """
    Choose a LoanInstrument name based on Impact, Risk, Return bands.
    Stage is *not* enforced for eligibility; it is only recorded in the
    created instrument's description for traceability.

    Returns a LoanInstrument (created if missing) or None if no rule matches.
    """
    from questionnaires.models import LoanInstrument  # local import to avoid cycles

    # ----- helpers -----
    def get_norm(code: str) -> float:
        sec = (details.get("sections") or {}).get(code, {})
        try:
            return float(sec.get("normalized", 0))
        except Exception:
            return 0.0

    def in_range(v: float, lo: float, hi: float) -> bool:
        return lo <= v <= hi

    I = get_norm("IMPACT")
    R = get_norm("RISK")
    Ret = get_norm("RETURN")

    impact_band = lambda lo, hi: in_range(I, lo, hi)
    risk_band   = lambda lo, hi: in_range(R, lo, hi)
    return_low  = lambda: in_range(Ret, 0, 33)
    return_mid  = lambda: in_range(Ret, 34, 66)
    return_high = lambda: in_range(Ret, 67, 100)

    name: str | None = None

    # -------- High return (67–100) --------
    if return_high():
        if impact_band(0, 20):
            if risk_band(0, 20):
                name = "Commercial debt"
            elif risk_band(21, 40):
                name = "Commercial debt / Equity"
            else:  # 41–100
                name = "Commercial equity"
        elif impact_band(21, 40):
            if risk_band(0, 40):
                name = "Commercial debt / Impact Linked financing"
            else:
                name = "Commercial equity"
        elif impact_band(41, 60):
            if risk_band(0, 40):
                name = "Commercial debt / Impact Linked financing"
            else:
                name = "Guarantee backed debt with TA"
        elif impact_band(61, 80):
            if risk_band(0, 40):
                name = "Commercial debt with impact linked incentives"
            elif risk_band(41, 60):
                name = "Guarantee backed debt with TA"
            elif risk_band(61, 80):
                name = "Subordinate / concessional equity / Convertible Note"
            else:
                name = "Returnable Grant"
        elif impact_band(81, 100):
            if risk_band(0, 40):
                name = "Commercial debt with impact linked incentives"
            elif risk_band(41, 60):
                name = "Debt linked instrument like convertible note"
            elif risk_band(61, 80):
                name = "Returnable Grant"
            else:
                name = "Grant"

    # -------- Mid return (34–66) --------
    if name is None and return_mid():
        if impact_band(0, 20):
            name = "Commercial Debt"
        elif impact_band(21, 40):
            if risk_band(0, 40):
                name = "Commercial debt  with impact linked financing like interest subvention"
            else:
                name = "Debt linked instrument like convertible note"
        elif impact_band(41, 60):
            if risk_band(0, 40):
                name = "Commercial debt / equity - Impact linked incentives"
            else:
                name = "Guarantee backed debt with TA"
        elif impact_band(61, 80):
            if risk_band(0, 40):
                name = "Commercial debt with impact linked incentives"
            elif risk_band(41, 80):
                name = "Concessional debt / Guarantee backed debt"
            else:
                name = "Returnable Grant"
        elif impact_band(81, 100):
            if risk_band(0, 20):
                name = "Commercial debt with impact linked incentives"
            elif risk_band(21, 40):
                name = "Guarantee backed debt"
            elif risk_band(41, 60):
                name = "Debt linked instrument like convertible note"
            elif risk_band(61, 80):
                name = "Guarantee backed debt with TA"
            else:
                name = "Returnable Grant"

    # -------- Low return (0–33) --------
    if name is None and return_low():
        if impact_band(0, 20):
            name = "Commercial debt"
        elif impact_band(21, 40):
            if risk_band(0, 40):
                name = "Commercial debt  with impact linked financing like interest subvention"
            else:
                name = "Guarantee backed debt with impact linked interest subvention"
        elif impact_band(41, 60):
            if risk_band(0, 40):
                name = "Commercial debt  with impact linked financing like interest subvention"
            else:
                name = "Guarantee backed debt with impact linked interest subvention"
        elif impact_band(61, 80):
            if risk_band(0, 40):
                name = "Concessional debt"
            elif risk_band(41, 80):
                name = "Guarantee backed debt with impact linked interest subvention"
            else:
                name = "Returnable Grant"
        elif impact_band(81, 100):
            if risk_band(0, 20):
                name = "Debt with Impact linked interest subvention"
            elif risk_band(21, 40):
                name = "Guarantee backed Debt with Impact linked interest subvention"
            elif risk_band(41, 60):
                name = "Debt linked instrument like convertible note"
            elif risk_band(61, 80):
                name = "Returnable Grant"
            else:
                name = "Grant"

    if not name:
        return None

    stage_str = (stage or "").upper()
    inst, _ = LoanInstrument.objects.get_or_create(
        name=name,
        defaults={"description": f"Auto-mapped for Impact={I:.0f}, Risk={R:.0f}, Return={Ret:.0f}, Stage={stage_str or '-'}"}
    )
    return inst


@transaction.atomic
def eligibility_check(assessment: Assessment, *, overall_threshold: Decimal = DEFAULT_OVERALL_PASS_THRESHOLD) -> LoanEligibilityResult:
    """
    Evaluates loan eligibility for a given Assessment and persists the result.
    Requires:
      - assessment.scores like: {"sections": {"IMPACT": X, "RISK": Y, "RETURN": Z}, "overall": ...}
      - EligibilityRule seeded for sections you care about (IMPACT/RISK/RETURN).
    Outcome:
      - Section gates (min/max) must pass.
      - Weighted overall >= overall_threshold to be eligible.
    """
    if not assessment.scores or "sections" not in assessment.scores:
        # No scores yet; mark ineligible with reason.
        return LoanEligibilityResult.objects.update_or_create(
            assessment=assessment,
            defaults={
                "overall_score": Decimal("0"),
                "is_eligible": False,
                "matched_instrument": None,
                "details": {
                    "reason": "Scores not available",
                    "sections": {},
                    "weights_sum": 0,
                },
                "evaluated_at": timezone.now(),
            },
        )[0]

    sec_scores: Dict[str, Decimal] = assessment.scores.get("sections", {}) or {}
    rules_by_code = _load_section_rules()

    # Build evaluation per section
    details = {"sections": {}, "weights_sum": 0}
    total_weighted = Decimal("0")
    weights_sum = Decimal("0")
    all_section_gates_pass = True

    for section in Section.objects.all().order_by("order"):
        code = section.code
        raw_score = sec_scores.get(code)

        # Only consider a section in weighting if it has a rule and a score
        if (code not in rules_by_code) or (raw_score is None):
            continue

        rule = rules_by_code[code]
        norm_score = _normalize_to_100(raw_score)
        w = Decimal(str(rule.weight or 0))
        min_t = Decimal(str(rule.min_threshold))
        max_t = Decimal(str(rule.max_threshold))

        # Special-case for RISK (lower is better)
        if code.upper() == "RISK":
            # keep RISK raw scale (usually 0–40) to avoid over-normalizing
            if raw_score is not None and Decimal(str(raw_score)) <= max_t:
                norm_score = Decimal(str(raw_score))
            gate_pass = norm_score <= max_t
        else:
            gate_pass = (norm_score >= min_t) and (norm_score <= max_t)

        # accumulate weighted overall only for sections with weight > 0
        contrib = Decimal("0")
        if w > 0:
            contrib = (norm_score * w) / Decimal("100")  # weight is still treated as percentage
            total_weighted += contrib
            weights_sum += w

        details["sections"][code] = {
            "raw": raw_score,
            "normalized": float(norm_score),
            "min": float(min_t),
            "max": float(max_t),
            "weight": float(w),
            "contribution": float(contrib),  # contribution on 0–100 scale
            "gate_pass": gate_pass,
            "criteria": rule.criteria or {},
            "recommendation": rule.recommendation or "",
        }

        if not gate_pass:
            all_section_gates_pass = False

    details["weights_sum"] = float(weights_sum)

    # If no rules contributed, cannot determine eligibility
    if weights_sum == 0:
        overall_score = Decimal("0")
        is_eligible = False
        details["reason"] = "No applicable rules or weights defined."
    else:
        # overall weighted score is already on 0–100 scale due to contribution math above
        overall_score = _clamp_0_100(total_weighted / (weights_sum / Decimal("100")))
        # Eligibility requires all gates pass AND overall >= threshold
        is_eligible = all_section_gates_pass and (overall_score >= overall_threshold)
        if not all_section_gates_pass:
            details["reason"] = "One or more section gates failed."
        elif overall_score < overall_threshold:
            details["reason"] = f"Overall score below threshold {overall_threshold}."

    org_stage = getattr(getattr(assessment, "organization", None), "org_stage", None)
    stage_str = (str(org_stage) if org_stage is not None else "").upper()
    details["stage"] = stage_str
    
    instrument = _pick_instrument(overall_score, details, stage=stage_str)

    # Persist & return
    obj, _ = LoanEligibilityResult.objects.update_or_create(
        assessment=assessment,
        defaults={
            "overall_score": overall_score,
            "is_eligible": is_eligible,
            "matched_instrument": instrument,
            "details": details,
            "evaluated_at": timezone.now(),
        },
    )
    return obj