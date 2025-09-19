from questionnaires.logic import evaluate_rule

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