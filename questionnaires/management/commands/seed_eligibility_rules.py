from django.core.management.base import BaseCommand
from pathlib import Path
import json

from questionnaires.models import Section, EligibilityRule

class Command(BaseCommand):
    help = "Seed EligibilityRule entries from JSON file"

    def handle(self, *args, **options):
        file_path = Path("seed/eligibility_rules.json")

        if not file_path.exists():
            self.stdout.write(self.style.ERROR("File eligibility_rules.json not found in seed_data/"))
            return

        with open(file_path, "r") as f:
            data = json.load(f)

        created, updated = 0, 0
        for rule in data:
            try:
                section = Section.objects.get(code=rule["section_code"])
            except Section.DoesNotExist:
                self.stdout.write(self.style.WARNING(f"Section {rule['section_code']} not found. Skipping."))
                continue

            obj, created_flag = EligibilityRule.objects.update_or_create(
                section=section,
                defaults={
                    "min_threshold": rule["min_threshold"],
                    "max_threshold": rule["max_threshold"],
                    "weight": rule["weight"],
                    "criteria": rule.get("criteria", {}),
                    "recommendation": rule.get("recommendation", "")
                }
            )
            if created_flag:
                created += 1
            else:
                updated += 1

        self.stdout.write(self.style.SUCCESS(f"Eligibility rules seeded: {created} created, {updated} updated"))