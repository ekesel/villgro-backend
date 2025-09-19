# questionnaire/logic.py
from typing import Any, Dict, List, Union

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