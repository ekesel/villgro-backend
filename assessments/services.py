from questionnaires.logic import evaluate_rule
from questionnaires.models import Section, Question
from questionnaires.utils import extract_q_refs

def build_answers_map(assessment):
    return {a.question.code: a.data for a in assessment.answers.select_related("question")}

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
    for q in Question.objects.prefetch_related("conditions").all():
        for cond in q.conditions.all():
            control |= extract_q_refs(cond.logic)
    return control