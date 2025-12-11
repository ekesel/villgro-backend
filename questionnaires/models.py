from __future__ import annotations
from decimal import Decimal
from django.db import models
from django.utils import timezone

class Section(models.Model):
    code = models.CharField(max_length=50, unique=True)  # e.g. IMPACT, RISK
    title = models.CharField(max_length=255)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return self.title


class Question(models.Model):
    TYPE_CHOICES = [
        ("SINGLE_CHOICE", "Single Choice"),
        ("MULTI_CHOICE", "Multi Choice"),
        ("SLIDER", "Slider"),
        ("MULTI_SLIDER", "Multi Slider"),
        ("RATING", "Rating"),
        ("NPS", "Net Promoter Score"),
    ]

    code = models.CharField(max_length=100, unique=True)
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name="questions")
    text = models.TextField()
    help_text = models.TextField(blank=True, null=True)
    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    required = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)
    max_score = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    weight = models.DecimalField(max_digits=5, decimal_places=2, default=1.0)
    is_active = models.BooleanField(default=True)
    sector = models.CharField(
        max_length=50,
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["section", "order"]

    def __str__(self):
        return f"{self.section.code} - {self.code}"


class AnswerOption(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="options")
    label = models.CharField(max_length=2500)
    value = models.CharField(max_length=2500)  # frontend key
    points = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    order = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.question.code} -> {self.label}"


class QuestionDimension(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="dimensions")
    code = models.CharField(max_length=50)  # e.g. "reach", "depth"
    label = models.CharField(max_length=255)
    min_value = models.IntegerField(default=0)
    max_value = models.IntegerField(default=10)
    points_per_unit = models.DecimalField(max_digits=6, decimal_places=2, default=1.0)
    weight = models.DecimalField(max_digits=5, decimal_places=2, default=1.0)

    def __str__(self):
        return f"{self.question.code} - {self.label}"


class BranchingCondition(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="conditions")
    logic = models.JSONField(help_text="JSON rule to evaluate visibility")

    def __str__(self):
        return f"Condition for {self.question.code}"
    

from django.db import models
from questionnaires.models import Section, Question
from assessments.models import Assessment

# ---------------------------
# 1. Loan Instruments (Static)
# ---------------------------
class LoanInstrument(models.Model):
    """
    Represents a loan product or instrument that NGOs can qualify for.
    Example: Working Capital Loan, Growth Fund, Bridge Loan, etc.
    """
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    min_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    max_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    interest_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    tenure_months = models.IntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


# -------------------------------------
# 2. Eligibility Rules (Section-Level)
# -------------------------------------
class EligibilityRule(models.Model):
    """
    Defines eligibility thresholds per Section (IMPACT, RISK, RETURN).
    Thresholds and weights are normalized out of 100.
    """
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name="eligibility_rules")
    min_threshold = models.DecimalField(max_digits=5, decimal_places=2, help_text="Minimum required section score (0–100 scale)")
    max_threshold = models.DecimalField(max_digits=5, decimal_places=2, help_text="Maximum acceptable section score (0–100 scale)")
    weight = models.DecimalField(max_digits=5, decimal_places=2, default=0, help_text="Weight contribution (0–100)")
    criteria = models.JSONField(default=dict, blank=True, help_text="Additional JSON-based criteria (like metrics or ranges)")
    recommendation = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.section.code} ({self.min_threshold}-{self.max_threshold})"


# -------------------------------------------------
# 3. Optional Fine-Grained Question-Level Rule Map
# -------------------------------------------------
class QuestionEligibilityRule(models.Model):
    """
    (Optional) Maps a specific Question to a loan eligibility modifier.
    Can be used for domain-specific weighting.
    """
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="eligibility_rules")
    multiplier = models.DecimalField(max_digits=5, decimal_places=2, default=1.0)
    max_points = models.DecimalField(max_digits=6, decimal_places=2, default=10.0)
    condition = models.JSONField(default=dict, blank=True, help_text="Conditional logic (if any)")

    def __str__(self):
        return f"{self.question.code} rule"


# ---------------------------------------------
# 4. Loan Eligibility Result (Stored per NGO)
# ---------------------------------------------
class LoanEligibilityResult(models.Model):
    """
    Stores computed eligibility outcome for an Assessment.
    """
    assessment = models.OneToOneField(Assessment, on_delete=models.CASCADE, related_name="loan_eligibility")
    overall_score = models.DecimalField(max_digits=5, decimal_places=2, help_text="Weighted overall score out of 100")
    is_eligible = models.BooleanField(default=False)
    matched_instrument = models.ForeignKey(LoanInstrument, on_delete=models.SET_NULL, null=True, blank=True)
    details = models.JSONField(default=dict, blank=True, help_text="Per-section evaluation breakdown")
    evaluated_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Eligibility for Assessment {self.assessment_id}"
    

class FundType(models.TextChoices):
    WORKING_CAPITAL = "WORKING_CAPITAL", "Working capital"
    GROWTH_CAPITAL  = "GROWTH_CAPITAL",  "Growth capital"
    EQUIPMENT       = "EQUIPMENT",       "Equipment/Asset purchase"
    BRIDGE          = "BRIDGE",          "Bridge financing"
    OTHER           = "OTHER",           "Other"

class LoanRequest(models.Model):
    class Status(models.TextChoices):
        DRAFT     = "DRAFT",     "Draft"
        SUBMITTED = "SUBMITTED", "Submitted"
        UNDER_REVIEW = "UNDER_REVIEW", "Under Review"
        APPROVED  = "APPROVED",  "Approved"
        REJECTED  = "REJECTED",  "Rejected"
        WITHDRAWN = "WITHDRAWN", "Withdrawn"

    assessment   = models.ForeignKey("assessments.Assessment", on_delete=models.PROTECT, related_name="loan_requests")
    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT, related_name="loan_requests")
    applicant    = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="loan_requests")

    # UI fields (from the Figma)
    founder_name  = models.CharField(max_length=255)
    founder_email = models.EmailField()
    amount_in_inr = models.DecimalField(max_digits=14, decimal_places=2)
    fund_type     = models.CharField(max_length=32, choices=FundType.choices)

    # snapshot: what we decided at submission time
    eligibility_overall  = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0"))
    eligibility_decision = models.BooleanField(default=False)  # True only if check passed at submit time
    eligibility_details  = models.JSONField(default=dict, blank=True)

    status       = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    submitted_at = models.DateTimeField(null=True, blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

class LoanStatusHistory(models.Model):
    request   = models.ForeignKey(LoanRequest, on_delete=models.CASCADE, related_name="history")
    from_status = models.CharField(max_length=20, choices=LoanRequest.Status.choices)
    to_status   = models.CharField(max_length=20, choices=LoanRequest.Status.choices)
    changed_by  = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="loan_status_changes")
    reason      = models.TextField(blank=True, null=True)
    created_at  = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]