def extract_q_refs(rule) -> set:
    if not rule:
        return set()
    refs = set()
    if isinstance(rule, dict):
        if "q" in rule:
            refs.add(rule["q"])
        for k in ("all", "any"):
            if k in rule and isinstance(rule[k], list):
                for r in rule[k]:
                    refs |= extract_q_refs(r)
        if "not" in rule:
            refs |= extract_q_refs(rule["not"])
    return refs

def _build_validation_message(exc_detail):
    """
    Turn DRF ValidationError.detail into a readable string like:
    "Please fix the highlighted fields. email: This field is required. password: This password is too short."
    """
    base = "Please fix the highlighted fields."
    detail = exc_detail

    # If it's already a string / list, just append
    if isinstance(detail, list):
        extra = " ".join(str(m) for m in detail)
        return f"{base} {extra}"
    if not isinstance(detail, dict):
        return f"{base} {detail}"

    # dict case: {"field": ["msg1", "msg2"], "non_field_errors": [...]}
    parts = []
    for field, messages in detail.items():
        label = "General" if field == "non_field_errors" else field
        if isinstance(messages, (list, tuple)):
            msg_text = " ".join(str(m) for m in messages)
        else:
            msg_text = str(messages)
        parts.append(f"{label}: {msg_text}")

    extra = " ".join(parts)
    return f"{base} {extra}" if extra else base