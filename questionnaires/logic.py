# questionnaire/logic.py
from __future__ import annotations
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from dataclasses import dataclass
from typing import Dict, Tuple, List, Optional, Any
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
    Normalize a numeric score to 0..100.

    IMPORTANT:
    - Your EligibilityRule thresholds are defined on a 0..100 scale.
    - Your section scores should be stored on 0..100 to avoid ambiguity.

    This function assumes:
    - If value is already 0..100, keep it.
    - If value is 0..1, treat it as ratio -> *100.
    Otherwise clamp.
    """
    try:
        d = Decimal(str(raw))
    except Exception:
        return Decimal("0")

    if Decimal("0") <= d <= Decimal("1"):
        return _clamp_0_100(d * Decimal("100"))

    # treat everything else as already-on-100 scale
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


@dataclass(frozen=True)
class InstrumentRule:
    name: str
    impact_range: Tuple[int, int]   # inclusive
    risk_range: Tuple[int, int]     # inclusive
    return_range: Tuple[int, int]   # inclusive
    text: str

INSTRUMENT_RULES: List[InstrumentRule] = [
    # -------------------- RETURN 67-100 --------------------
    InstrumentRule("Commercial debt", (0, 20), (0, 20), (67, 100),
        "The business appears to be a purely commercial opportunity with no blended finance rationale. There is no role for philanthropic capital currently. Given the low risk and high return potential, commercial debt seems appropriate for funding"),
    InstrumentRule("Commercial debt / Equity", (0, 20), (21, 40), (67, 100),
        "The business appears to be a purely commercial opportunity with no blended finance rationale. There is no role for philanthropic capital currently. Given the low-moderate risk and high return potential, commercial debt / equity seems appropriate for funding"),
    InstrumentRule("Commercial equity", (0, 20), (41, 60), (67, 100),
        "The business is financially appealing but with limited development relevance. Philanthropic capital doesn't have a role to play here; commercial debt / equity investment seems more relevant given risk levels are moderate."),
    InstrumentRule("Commercial equity", (0, 20), (61, 80), (67, 100),
        "The business is financially appealing but with limited development relevance. Philanthropic capital doesn't have a role to play here; commercial equity investment seems more relevant given risk levels are high."),
    InstrumentRule("Commercial equity", (0, 20), (81, 100), (67, 100),
        "The business is financially appealing but with limited development relevance. Philanthropic capital doesn't have a role to play here; commercial equity investment seems more relevant given risk levels are high."),

    InstrumentRule("Commercial debt / Impact Linked financing", (21, 40), (0, 20), (67, 100),
        "The business has moderate social relevance with strong commercial potential and low risk. Commercial capital can lead confidently, supported by light impact-linked incentives to enable stronger social impact."),
    InstrumentRule("Commercial debt / Impact Linked financing", (21, 40), (21, 40), (67, 100),
        "The enterprise demonstrates a sustainable model serving some underserved users. Returns justify commercial investment, with manageable risk. Commercial financing with impact-linked features strengthens inclusion."),
    InstrumentRule("Commercial equity", (21, 40), (41, 60), (67, 100),
        "The business has reasonable impact intent and a strong commercial case. Philanthropic subsidy is not required. Commercial equity is the right structure for scaling. Philanthropy may come in with specific impact linked financing"),
    InstrumentRule("Commercial equity", (21, 40), (61, 80), (67, 100),
        "Impact value is present but not strong, while business risk remains high. Upside potential exists for commercial equity investors with higher risk appetite. Philanthropy may come in for specific impact objectives only"),
    InstrumentRule("Commercial equity", (21, 40), (81, 100), (67, 100),
        "The business offers commercial upside with limited social additionality. High risk suggests that  commercially driven equity might be appropriate and participate."),

    InstrumentRule("Commercial debt / Impact Linked financing", (41, 60), (0, 20), (67, 100),
        "The business appears to have clear market traction and meaningful social value. Low risk and strong commercial returns make commercial capital appropriate, supported by incentives from philanthropy that further propel impact."),
    InstrumentRule("Commercial debt / Impact Linked financing", (41, 60), (21, 40), (67, 100),
        "The business appears to have a solid model with credible social outcomes and commercial potential and moderate risk. Commercial financing can take the lead, with selective incentives from philanthropy to further strengthen impact focus"),
    InstrumentRule("Guarantee backed debt with TA", (41, 60), (41, 60), (67, 100),
        "The enterprise appears to have a good impact focus coupled with solid business promise, but moderately high risk. A philanthropic guarantee to unlock commercial debt together with technical assistance can help accelerate investor confidence and impact delivery."),
    InstrumentRule("Guarantee backed debt with TA", (41, 60), (61, 80), (67, 100),
        "The business provides considerable social benefit with strong returns but heightened risk. A philanthropic guarantee and operational strengthening through TA are suitable to crowd in lenders."),
    InstrumentRule("Guarantee backed debt with TA", (41, 60), (81, 100), (67, 100),
        "While impact and returns are promising, risk is very high. A philanthropy backed guarantee mechanism with TA offers appropriate de-risking for commercial participation."),

    InstrumentRule("Commercial debt with impact linked incentives", (61, 80), (0, 20), (67, 100),
        "The enterprise generates strong impact and operates with low execution risk while offering attractive commercial returns. Commercial capital can lead confidently, with philanthropy paying only for specific impact outcomes."),
    InstrumentRule("Commercial debt with impact linked incentives", (61, 80), (21, 40), (67, 100),
        "The business delivers meaningful outcomes and strong commercial potential. Moderate risk allows commercial financing with  with philanthropy paying only for specific impact outcomes."),
    InstrumentRule("Guarantee backed debt with impact linked interest subvention", (61, 80), (41, 60), (67, 100),
        "The enterprise has high impact and strong return potential, but risk levels may deter lenders. Philanthropy can play a crucial role in bringing risk mitigation through a partial guarantee to unlock commercial debt for furthering impact objectives."),
    InstrumentRule("Subordinate / concessional equity / Convertible Note", (61, 80), (61, 80), (67, 100),
        "The business is impactful and financially compelling, yet risk is high. Subordinated or concessional impact equity orconvertible capital is most appropriate to take the benefit of the economic upside and high impact potential ."),
    InstrumentRule("Returnable Grant", (61, 80), (81, 100), (67, 100),
        "The enterprise has strong impact and upside potential but very high risk. The high risk and impact and return potential make it a candidate for returnable grant where the grant can support innovation and absorb risk but also keep the chances of recovering it to further the purpose due to the high return potential."),

    InstrumentRule("Commercial debt with impact linked incentives", (81, 100), (0, 20), (67, 100),
        "Very strong impact and a robust commercial case with low risk. Commercial capital is suitable, with incentives ensuring depth of outreach"),
    InstrumentRule("Commercial debt with impact linked incentives", (81, 100), (21, 40), (67, 100),
        "The enterprise appears to have a very strong impact delivery prospect, addressing  a critical need and shows strong growth prospects. Given their high return and low impact, both commercial debt and impact investments are the right capital. Philanthropy can play the limited role of paying for pre-defined Impact."),
    InstrumentRule("Subordinate / concessional equity / Convertible Note", (81, 100), (41, 60), (67, 100),
        "The enterprise appears to have a very strong impact delivery prospect and strong return potential but moderate risk remains. Flexible equity and equity linked instruments (impact investments) are most suitable for growth capital with commercial debt for working capital."),
    InstrumentRule("Returnable Grant", (81, 100), (61, 80), (67, 100),
        "The business showcases strong mission alignment and upside potential with elevated operational challenges and risk. A returnable grant will be most suitable to support innovation and take the risk to suitably grow and derisk the business while being able to return the capital at the appropriate time."),
    InstrumentRule("Grant", (81, 100), (81, 100), (67, 100),
        "The enterprise tackles a critical development gap with significant risk and uncertain investability. Philanthropic capital is needed to play a critical role and to test viability before commercial investors engage."),

    # -------------------- RETURN 34-66 --------------------
    InstrumentRule("Commercial Debt", (0, 20), (0, 20), (34, 66),
        "The business appears to be commercially viable with limited focus on social impact. Standard investment structures apply without blended features. As return potential is medium, commercial debt seems to be the appropriate funding instrument for you"),
    InstrumentRule("Commercial Debt", (0, 20), (21, 40), (34, 66),
        "Impact is limited and financial performance is moderate. Risk levels allow commercial lenders to participate directly, making commercial debt a suitable funding option."),
    InstrumentRule("Commercial Debt", (0, 20), (41, 60), (34, 66),
        "The business has a middle-of-the-road financial prospect with low impact contribution. This falls outside the scope of blended finance and should rely solely on commercial capital, likely debt given the moderate return."),
    InstrumentRule("Commercial Debt", (0, 20), (61, 80), (34, 66),
        "The business has limited social contribution and elevated operational risk. Moderate returns do not justify subsidy. Commercial debt should be considered cautiously based on risk appetite."),
    InstrumentRule("Commercial Debt", (0, 20), (81, 100), (34, 66),
        "The opportunity is commercially oriented but comes with significant business risk. Limited social impact does not justify concessional support; commercial capital is more suited buy there is need to address the risk exposure to attract the same."),

    InstrumentRule("Commercial debt with impact linked financing like interest subvention", (21, 40), (0, 20), (34, 66),
        "A commercially viable business with moderate impact. Moderate returns with low risk support commercial debt. Impact-linked philanthropic funding may be possible for specific impact objectives."),
    InstrumentRule("Commercial debt with impact linked financing like interest subvention", (21, 40), (21, 40), (34, 66),
        "The business appears to have  moderate impact, risk and return. A commercial loan with targeted incentives from philanthropy can support continued reach in underserved markets."),
    InstrumentRule("Debt linked instrument like convertible note", (21, 40), (41, 60), (34, 66),
        "The enterprise has some development value but moderate to high risk and returns. Debt linked instruments such as convertible notes are appropriate. Role of philanthropy can be designed specific to the impact objectives"),
    InstrumentRule("Debt linked instrument like convertible note", (21, 40), (61, 80), (34, 66),
        "Based on our assessment, moderate impact combined with elevated risk makes conventional lending difficult. Debt and debt linked instruments such as convertible notes are appropriate. Role of philanthropy can be designed specifically to bring risk mitigation for specific impact objectives"),
    InstrumentRule("Debt linked instrument like convertible note", (21, 40), (81, 100), (34, 66),
        "Based on our assessment, moderate impact combined with high risk makes conventional funding difficult. Debt and debt linked instruments such as convertible notes are appropriate. Role of philanthropy can be designed specifically to bring risk mitigation for specific impact objectives"),

    InstrumentRule("Commercial debt / equity - Impact linked incentives", (41, 60), (0, 20), (34, 66),
        "The business has meaningful impact and reasonable financial performance with low risk. Commercial investment is justified, with incentives from philanthropy ensuring continuous focus on target impact groups."),
    InstrumentRule("Commercial debt / equity - Impact linked incentives", (41, 60), (21, 40), (34, 66),
        "The business has meaningful impact and reasonable financial performance with low - moderate risk. Commercial debt seems to be most appropriate, aided by modest incentives by philanthropy linked to verified outcomes."),
    InstrumentRule("Guarantee backed debt with TA", (41, 60), (41, 60), (34, 66),
        "The business has meaningful Impact and returns are moderate but risk warrants caution. A risk-backed structure like loan backed by philanthropic guarantee together with TA will build resilience and creditworthiness."),
    InstrumentRule("Guarantee backed debt with TA", (41, 60), (61, 80), (34, 66),
        "The business addresses relevant impact needs but faces notable uncertainty. A risk-backed structure like loan backed by philanthropic guarantee together with TA will build resilience and creditworthiness."),
    InstrumentRule("Guarantee backed debt with TA", (41, 60), (81, 100), (34, 66),
        "The social impact case is reasonably good but risks are high. A philanthropic guarantee to mitigate risks for debt investors and hands-on support through TA to support business growth and bring stability will be necessary to unlock commercial and impact potential."),

    InstrumentRule("Commercial debt with impact linked incentives", (61, 80), (0, 20), (34, 66),
        "The business appears to be a financially stable model with substantial impact and manageable returns. Commercial debt is suitable. Philanthropy can be very intentional in the role it plays - paying for the specific impact objectives it wants to achieve."),
    InstrumentRule("Commercial debt with impact linked incentives", (61, 80), (21, 40), (34, 66),
        "The business demonstrates a strong-impact potential with acceptable risk and moderate returns. Philanthropy can be very intentional in the role it plays - paying for the specific impact objectives it wants to achieve."),
    InstrumentRule("Concessional debt / Guarantee backed debt", (61, 80), (41, 60), (34, 66),
        "The business delivers notable impact but faces considerable risk. Concessional or guarantee-backed debt can manage risk and cost while enabling impact at scale."),
    InstrumentRule("Concessional debt / Guarantee backed debt", (61, 80), (61, 80), (34, 66),
        "Impact outcomes are strong, but risk and returns do not fully align. A risk-backed structure like loan backed by philanthropic guarantee or concessional loans together with TA will build resilience and creditworthiness to scale impact."),
    InstrumentRule("Returnable Grant", (61, 80), (81, 100), (34, 66),
        "Social value is high but risk remains substantial. A returnable grant where the grant can support innovation and absorb risk but also retain the opportunity of recovering the funding to further the purpose due to the moderate return potential."),

    InstrumentRule("Commercial debt with impact linked incentives", (81, 100), (0, 20), (34, 66),
        "The business has strong impact potential and low risk and moderate returns which supports a stable case for commercial lending. Philanthropy can have a very targeted role to pay for impact ro bring in affordability."),
    InstrumentRule("Guarantee backed debt", (81, 100), (21, 40), (34, 66),
        "The business has high social value but moderate risk and returns. Risk-sharing through a philanthropy backed guarantee can enable commercial debt while keeping pricing accessible."),
    InstrumentRule("Debt linked instrument like convertible note", (81, 100), (41, 60), (34, 66),
        "The model delivers meaningful impact but profitability is not yet proven and risk is moderately high. Debt linked instruments like convertible notes allows flexibile capital for the enterprise and investor while funding impact creation."),
    InstrumentRule("Guarantee backed debt with TA", (81, 100), (61, 80), (34, 66),
        "The business appears to have high impact potential but also has high risk with moderate returns.  A  philanthropy backed guarantee along with technical assistance is most appropriate to strengthens execution and unlock commercial lender participation."),
    InstrumentRule("Returnable Grant", (81, 100), (81, 100), (34, 66),
        "Impact is compelling, but the commercial path remains unclear due to the substantial risk . A returnable grant enables commercial validation with future repayment potential."),

    # -------------------- RETURN 0-33 --------------------
    InstrumentRule("Commercial debt", (0, 20), (0, 20), (0, 33),
        "The model offers minimal impact and low financial upside. Concessional capital is unlikely unless linked specifically to clear impact objectives. Low returns may not be very attractive for commercial capital."),
    InstrumentRule("Commercial debt", (0, 20), (21, 40), (0, 33),
        "The business appears to have limited impact and constrained returns reduce the appeal for both philanthropy and commercial capital. Any funding support should be highly targeted and performance-based."),
    InstrumentRule("Commercial debt", (0, 20), (41, 60), (0, 33),
        "The business shows low development relevance and weak profitability. Commercial participation is unlikely given the moderate risk but low return, and subsidy is not justified beyond minimal ecosystem strengthening if needed."),
    InstrumentRule("Commercial debt", (0, 20), (61, 80), (0, 33),
        "High operational uncertainty combined with low returns makes investment challenging. The case for philanthropy to fund this is minimal; it is recommended that strategic reconsideration be done."),
    InstrumentRule("Commercial debt", (0, 20), (81, 100), (0, 33),
        "Based on the inputs, business is showing low impact, weak return visibility, and very high risk, therefore neither commercial nor concessional capital is justified. Business model revision should precede investment."),

    InstrumentRule("Commercial debt with impact linked financing like interest subvention", (21, 40), (0, 20), (0, 33),
        "Impact is moderate but returns are limited. Commercial debt may still be viable with thoughtful risk management and targeted impact support to bring concessionality."),
    InstrumentRule("Commercial debt with impact linked financing like interest subvention", (21, 40), (21, 40), (0, 33),
        "The business appears to have low returns with low-moderate social impact. Commercial debt is more suitable with specific  performance-based support from philanthropy to bring concessionality."),
    InstrumentRule("Guarantee backed debt with impact linked interest subvention", (21, 40), (41, 60), (0, 33),
        "The business appears to have a modest-impact model with uncertain / low returns and moderate to high risk. Philanthropy backed guarantee mechanism may be appropriate to unlock debt for furthering impact with additional incentives for impact creation."),
    InstrumentRule("Guarantee backed debt with impact linked interest subvention", (21, 40), (61, 80), (0, 33),
        "The enterprise delivers some social benefits but risk and returns are not well aligned. Risk-sharing structures are the only cautious path for debt capital if there is sufficient case for philanthropy to enter."),
    InstrumentRule("Guarantee backed debt with impact linked interest subvention", (21, 40), (81, 100), (0, 33),
        "The business shows high uncertainty, moderate impact and low profitability which limit both commercial and concessional logic. Support in the form of philanthropy guaranteeing loans or providing concessionality must be closely tied to measurable progress."),

    InstrumentRule("Commercial debt with impact linked financing like interest subvention", (41, 60), (0, 20), (0, 33),
        "Impact outcomes are meaningful but financial returns are low. As risk is low, commercial debt seems most appropriate. Philanthropy can play a crucial role in bringing concessionality to address the low return to ensure business and impact sustabinability."),
    InstrumentRule("Commercial debt with impact linked financing like interest subvention", (41, 60), (21, 40), (0, 33),
        "Some social value exists but returns are weak. As risk is low, commercial debt seems most appropriate. Philanthropy can play a crucial role in bringing concessionality to address the low return to ensure business and impact sustabinability."),
    InstrumentRule("Guarantee backed debt with impact linked interest subvention", (41, 60), (41, 60), (0, 33),
        "The business appears to have moderate impact with moderate to high risk and low return. A philanthropic guarantee paired with performance-linked incentives can allow commercial lenders to fund and further the impact."),
    InstrumentRule("Guarantee backed debt with impact linked interest subvention", (41, 60), (61, 80), (0, 33),
        "The business appears to have a good impact focus but faces considerable risk and weak returns. Cautious risk-sharing like philanthropic guarantee paired with performance-linked incentives can allow commercial lenders to fund and further the impact. "),
    InstrumentRule("Guarantee backed debt with impact linked interest subvention", (41, 60), (81, 100), (0, 33),
        "With high uncertainty and modest impact outcomes paired with low returns, efforts are needed to reduce the risk. Technical support and a deep dive into the business risk to enable that is crucial. Given the good impact focus, philanthropy can support with careful risk mitigation to unlock debt and bring in concessionality linked to impact delivery."),

    InstrumentRule("Concessional debt", (61, 80), (0, 20), (0, 33),
        "The business appears to have a high impact potential with low risk and returns.  Low risk and return makes concessional lending appropriate to sustain affordability without heavy exposure to expensive commercial capital."),
    InstrumentRule("Concessional debt", (61, 80), (21, 40), (0, 33),
        "The business offers strong impact potential but limited financial delivery. Low risk and return makes concessional lending appropriate to sustain affordability without heavy exposure to expensive commercial capital."),
    InstrumentRule("Guarantee backed debt with impact linked interest subvention", (61, 80), (41, 60), (0, 33),
        "The business demonstrates strong social relevance with uncertain financial resilience and moderately high risk. A partial guarantee backed by philanthropy with targeted concessionality can help reach impact objectives effectively."),
    InstrumentRule("Guarantee backed debt with impact linked interest subvention", (61, 80), (61, 80), (0, 33),
        "The business demonstrates strong social relevance with uncertain financial resilience and high risk. A partial guarantee backed by philanthropy with targeted concessionality can help reach impact objectives effectively."),
    InstrumentRule("Returnable Grant", (61, 80), (81, 100), (0, 33),
        "Social outcomes are strong but commercial viability is unclear and risk is high. A returnable grant supports early validation and will allow the business to take necessary risk while keeping the option of recycling the capital. "),

    InstrumentRule("Debt with Impact linked interest subvention", (81, 100), (0, 20), (0, 33),
        "The business appears to have very high impact potential with low risk and returns. Specific impact linked concessional financing ensures sustained capital access for the target segment."),
    InstrumentRule("Guarantee backed Debt with Impact linked interest subvention", (81, 100), (21, 40), (0, 33),
        "The business appears to have strong social outcomes potential but insufficient returns for commercial funders. A  philanthropy backed guarantee to unlock loan with targeted incentives to bring concessionality will enable high impact creation."),
    InstrumentRule("Debt linked instrument like convertible note", (81, 100), (41, 60), (0, 33),
        "The business shows significant impact potential with weak returns and moderate risk. Debt linked instruments like convertible notes allows flexibile capital for the enterprise and investor while funding impact creation."),
    InstrumentRule("Returnable Grant", (81, 100), (61, 80), (0, 33),
        "The business appears to have a strong impact but uncertain viability and elevated risk. A returnable grant ensures catalytic support and allows for the enterprise to take the risk needed whle having a path toward repayment for philanthropy."),
    InstrumentRule("Grant", (81, 100), (81, 100), (0, 33),
        "The enterprise addresses a critical development challenge but faces considerable uncertainty and limited returns. Philanthropic capital is required to validate the model before commercial investment is feasible. A plain grant with specific milestones and objectives is necessary at this stage"),
]


def _pick_instrument(
    overall_score: Decimal,
    details: Dict,
    *,
    stage: str | None = None,
):
    """
    Pick a LoanInstrument based on Impact/Risk/Return bands (0..100 scale),
    and attach the exact narrative text from INSTRUMENT_RULES.

    IMPORTANT:
    - This expects details["sections"][CODE]["normalized"] to be 0..100 for:
      IMPACT, RISK, RETURN
    - RISK here is LOWER = better (risk level), NOT inverted.
    """
    from questionnaires.models import LoanInstrument  # local import to avoid cycles

    def get_norm(code: str) -> float:
        sec = (details.get("sections") or {}).get(code, {})
        try:
            v = sec.get("normalized", 0)
            return float(v) if v is not None else 0.0
        except Exception:
            return 0.0

    def in_range(v: float, lo: int, hi: int) -> bool:
        return lo <= v <= hi

    I = get_norm("IMPACT")
    R = get_norm("RISK")
    Ret = get_norm("RETURN")

    matched: Optional[InstrumentRule] = None
    for rule in INSTRUMENT_RULES:
        if (
            in_range(I, rule.impact_range[0], rule.impact_range[1])
            and in_range(R, rule.risk_range[0], rule.risk_range[1])
            and in_range(Ret, rule.return_range[0], rule.return_range[1])
        ):
            matched = rule
            break

    if not matched:
        return None

    desc = matched.text

    inst, created = LoanInstrument.objects.get_or_create(
        name=matched.name,
        defaults={"description": desc},
    )

    # If mapping text changes, keep DB consistent.
    if (not created) and (inst.description != desc):
        inst.description = desc
        inst.save(update_fields=["description"])

    return inst


@transaction.atomic
def eligibility_check(assessment, *, overall_threshold: Decimal = DEFAULT_OVERALL_PASS_THRESHOLD):
    if not assessment.scores or "sections" not in assessment.scores:
        obj, _ = LoanEligibilityResult.objects.update_or_create(
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
        )
        return obj

    sec_scores = assessment.scores.get("sections", {}) or {}
    rules_by_code = _load_section_rules()

    details = {"sections": {}, "weights_sum": 0}
    total_weighted = Decimal("0")
    weights_sum = Decimal("0")
    all_section_gates_pass = True

    for section in Section.objects.all().order_by("order"):
        code = section.code
        if code not in rules_by_code:
            continue

        raw_score = sec_scores.get(code)
        if raw_score is None:
            # rule exists but score missing -> fail gate
            all_section_gates_pass = False
            details["sections"][code] = {
                "raw": None,
                "normalized": None,
                "min": float(rules_by_code[code].min_threshold),
                "max": float(rules_by_code[code].max_threshold),
                "weight": float(rules_by_code[code].weight or 0),
                "contribution": 0.0,
                "gate_pass": False,
                "criteria": rules_by_code[code].criteria or {},
                "recommendation": rules_by_code[code].recommendation or "",
            }
            continue

        rule = rules_by_code[code]
        score_0_100 = _clamp_0_100(Decimal(str(raw_score)))  # already 0..100
        w = Decimal(str(rule.weight or 0))
        min_t = Decimal(str(rule.min_threshold))
        max_t = Decimal(str(rule.max_threshold))

        # Gate check on 0..100 for ALL sections (including RISK).
        # RISK lower-better is naturally enforced because max_t is low (e.g., 40).
        gate_pass = (score_0_100 >= min_t) and (score_0_100 <= max_t)

        contrib = Decimal("0")
        if w > 0:
            contrib = (score_0_100 * w) / Decimal("100")
            total_weighted += contrib
            weights_sum += w

        details["sections"][code] = {
            "raw": float(score_0_100),
            "normalized": float(score_0_100),
            "min": float(min_t),
            "max": float(max_t),
            "weight": float(w),
            "contribution": float(contrib),
            "gate_pass": gate_pass,
            "criteria": rule.criteria or {},
            "recommendation": rule.recommendation or "",
        }

        if not gate_pass:
            all_section_gates_pass = False

    details["weights_sum"] = float(weights_sum)

    if weights_sum == 0:
        overall_score = Decimal("0")
        is_eligible = False
        details["reason"] = "No applicable rules or weights defined."
    else:
        overall_score = _clamp_0_100(total_weighted / (weights_sum / Decimal("100")))
        is_eligible = all_section_gates_pass and (overall_score >= overall_threshold)

        if not all_section_gates_pass:
            details["reason"] = "One or more section gates failed."
        elif overall_score < overall_threshold:
            details["reason"] = f"Overall score below threshold {overall_threshold}."

    org_stage = getattr(getattr(assessment, "organization", None), "org_stage", None)
    stage_str = (str(org_stage) if org_stage is not None else "").upper()
    details["stage"] = stage_str

    instrument = _pick_instrument(overall_score, details, stage=stage_str)

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