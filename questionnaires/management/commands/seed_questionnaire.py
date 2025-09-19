# questionnaires/management/commands/seed_questionnaire.py
import json
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from questionnaires.models import Section, Question, AnswerOption, QuestionDimension, BranchingCondition
from django.db import transaction

class Command(BaseCommand):
    help = "Seed Sections/Questions/Options/Dimensions/Conditions from a JSON file."

    def add_arguments(self, parser):
        parser.add_argument("--file", "-f", type=str, required=True, help="Path to JSON file")

    @transaction.atomic
    def handle(self, *args, **opts):
        path = Path(opts["file"])
        if not path.exists():
            raise CommandError(f"File not found: {path}")

        data = json.loads(path.read_text(encoding="utf-8"))

        sections = data.get("sections", [])
        self.stdout.write(self.style.NOTICE(f"Seeding {len(sections)} sections..."))

        for s in sections:
            sec, _ = Section.objects.update_or_create(
                code=s["code"],
                defaults={"title": s.get("title", s["code"]), "order": s.get("order", 0)},
            )

            for q in s.get("questions", []):
                q_obj, _ = Question.objects.update_or_create(
                    code=q["code"],
                    defaults={
                        "section": sec,
                        "text": q["text"],
                        "help_text": q.get("help_text"),
                        "type": q["type"],
                        "required": q.get("required", True),
                        "order": q.get("order", 0),
                        "max_score": q.get("max_score"),
                        "weight": q.get("weight", 1.0),
                    },
                )

                # Options (replace all)
                AnswerOption.objects.filter(question=q_obj).delete()
                for opt in q.get("options", []):
                    AnswerOption.objects.create(
                        question=q_obj,
                        label=opt["label"],
                        value=opt["value"],
                        points=opt.get("points", 0),
                    )

                # Dimensions (replace all)
                QuestionDimension.objects.filter(question=q_obj).delete()
                for dim in q.get("dimensions", []):
                    QuestionDimension.objects.create(
                        question=q_obj,
                        code=dim["code"],
                        label=dim.get("label", dim["code"]),
                        min_value=dim.get("min", 0),
                        max_value=dim.get("max", 10),
                        points_per_unit=dim.get("points_per_unit", 1.0),
                        weight=dim.get("weight", 1.0),
                    )

                # Conditions (replace all)
                BranchingCondition.objects.filter(question=q_obj).delete()
                for cond in q.get("conditions", []):
                    BranchingCondition.objects.create(question=q_obj, logic=cond)

        self.stdout.write(self.style.SUCCESS("Questionnaire seeding complete."))