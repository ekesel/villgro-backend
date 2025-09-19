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