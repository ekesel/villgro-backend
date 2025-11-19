from django.db import models
from django.conf import settings
from django.contrib.postgres.fields import ArrayField  # Postgres

class Organization(models.Model):
    class RegistrationType(models.TextChoices):
        PRIVATE_LTD = "PRIVATE_LTD", "Private Limited Company"
        PUBLIC_LTD = "PUBLIC_LTD", "Public Limited Company"
        PARTNERSHIP = "PARTNERSHIP", "Partnership Firm"
        SOLE_PROP   = "SOLE_PROP", "Sole Proprietorship"

    name = models.CharField(max_length=255)
    date_of_incorporation = models.DateField(null=True, blank=True)
    gst_number = models.CharField(max_length=50, blank=True)
    cin_number = models.CharField(max_length=50, blank=True)
    registration_type = models.CharField(max_length=20, choices=RegistrationType.choices)
    created_by = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="organization")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # STEP 2
    class InnovationType(models.TextChoices):
        PRODUCT = "PRODUCT", "Product-based"
        PROCESS = "PROCESS", "Process-based"
        SERVICE = "SERVICE", "Service-based"

    class GeoScope(models.TextChoices):
        LOCALITY  = "LOCALITY", "A specific locality"
        DISTRICTS = "DISTRICTS", "Across one or more districts"
        STATES    = "STATES", "Across one or more states"
        PAN_INDIA = "PAN_INDIA", "PAN India"

    type_of_innovation = models.CharField(max_length=20, choices=InnovationType.choices, null=True, blank=True)
    geo_scope = models.CharField(max_length=20, choices=GeoScope.choices, null=True, blank=True)
    top_states = ArrayField(models.CharField(max_length=64), size=5, default=list, blank=True)

    # STEP 3
    class FocusSector(models.TextChoices):
        AGRICULTURE = "AGRICULTURE", "Agriculture"
        WASTE       = "WASTE", "Waste management / recycling"
        HEALTH      = "HEALTH", "Health"
        LIVELIHOOD  = "LIVELIHOOD", "Livelihood creation"
        OTHERS      = "OTHERS", "Others"

    class OrgStage(models.TextChoices):
        PROTOTYPE     = "PROTOTYPE", "Prototype"
        PRE_REVENUE   = "PRE_REVENUE", "Product ready - pre revenue"
        EARLY_REVENUE = "EARLY_REVENUE", "Early revenue"
        GROWING       = "GROWING", "Growing and scaling"

    class ImpactFocus(models.TextChoices):
        SOCIAL = "SOCIAL", "Social Impact"
        ENV    = "ENV", "Environmental Impact"
        BOTH   = "BOTH", "Both"

    focus_sector = models.CharField(max_length=20, choices=FocusSector.choices, null=True, blank=True)
    org_stage = models.CharField(max_length=20, choices=OrgStage.choices, null=True, blank=True)
    impact_focus = models.CharField(max_length=10, choices=ImpactFocus.choices, null=True, blank=True)
    annual_operating_budget = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)

    class UseOfQuestionnaire(models.TextChoices):
        FUNDING = "FUNDING", "Funding"
        SELF    = "SELF", "Self assessment"

    use_of_questionnaire = models.CharField(max_length=10, choices=UseOfQuestionnaire.choices, null=True, blank=True)
    received_philanthropy_before = models.BooleanField(null=True, blank=True)

    
class OnboardingProgress(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="onboarding",
    )
    # 1..3 for now
    current_step = models.PositiveSmallIntegerField(default=1)
    # arbitrary JSON to stash partial UI state/payloads (drafts)
    data = models.JSONField(default=dict, blank=True)
    is_complete = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    def bump_to(self, step: int, max_step: int = 3):
        step = max(1, min(step, max_step))
        if step > self.current_step:
            self.current_step = step