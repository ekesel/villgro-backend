# questionnaires/management/commands/create_dummy_questions.py

from django.core.management.base import BaseCommand
from django.db.models import Max
from django.utils.text import slugify

from questionnaires.models import Section, Question


class Command(BaseCommand):
    help = "Create one dummy question for each section (IMPACT, RISK, RETURN, SECTOR_MATURITY) in each sector."

    # SECTION codes we care about
    SECTION_CODES = ["IMPACT", "RISK", "RETURN", "SECTOR_MATURITY"]

    def add_arguments(self, parser):
        parser.add_argument(
            "--sectors",
            nargs="+",
            type=str,
            help=(
                "List of sector codes/names to create dummy questions for. "
                "If omitted, sectors will be inferred from existing questions."
            ),
        )

    def handle(self, *args, **options):
        sectors = options.get("sectors")

        # 1) Decide sectors
        if sectors:
            sector_list = [s.strip() for s in sectors if s.strip()]
        else:
            # Infer from existing questions' sector values
            self.stdout.write(self.style.WARNING("No sectors passed; inferring from existing Question.sector values."))
            sector_list = (
                Question.objects.exclude(sector__isnull=True)
                                .exclude(sector="")
                                .values_list("sector", flat=True)
                                .distinct()
            )

        if not sector_list:
            self.stdout.write(self.style.ERROR("No sectors found to process. Exiting."))
            return

        # 2) Fetch sections
        sections = {
            s.code.upper(): s
            for s in Section.objects.filter(code__in=self.SECTION_CODES)
        }

        missing_codes = [c for c in self.SECTION_CODES if c not in sections]
        if missing_codes:
            self.stdout.write(
                self.style.ERROR(f"Missing Section objects for codes: {', '.join(missing_codes)}")
            )
            return

        created_count = 0

        for sector in sector_list:
            self.stdout.write(f"Processing sector: {sector}")

            for code in self.SECTION_CODES:
                section = sections[code]

                # Check if there is already any question for this section+sector
                existing_qs = Question.objects.filter(section=section, sector=sector)

                if existing_qs.exists():
                    # If you strictly want exactly ONE dummy only when NONE exists, skip:
                    self.stdout.write(f"  - Section {code}: already has {existing_qs.count()} question(s), skipping.")
                    continue

                # Compute next order within this section+sector bucket
                max_order = (
                    Question.objects.filter(section=section, sector=sector)
                    .aggregate(m=Max("order"))["m"]
                    or 0
                )
                new_order = max_order + 1

                # Generate a unique code
                base_code = f"DUMMY_{code}_{slugify(sector).upper()}"
                code_candidate = base_code
                idx = 1
                while Question.objects.filter(code=code_candidate).exists():
                    code_candidate = f"{base_code}_{idx}"
                    idx += 1

                q = Question.objects.create(
                    section=section,
                    code=code_candidate,
                    text=f"Dummy {code} question for sector {sector}",
                    help_text="Auto generated dummy question for initial setup.",
                    type="SINGLE_CHOICE",  # <-- change if your type choices differ
                    required=False,
                    order=new_order,
                    max_score=0,
                    weight=1,
                    is_active=True,
                    sector=sector,
                )

                self.stdout.write(f"  + Created question {q.id} for section={code}, sector={sector}")
                created_count += 1

        self.stdout.write(self.style.SUCCESS(f"Done. Created {created_count} dummy questions."))