from django.db import models
from django.utils import timezone
from django.contrib.postgres.fields import ArrayField
from questionnaires.models import Question

class Assessment(models.Model):
    STATUS_CHOICES = [
        ("DRAFT", "Draft"),
        ("SUBMITTED", "Submitted"),
    ]

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="assessments")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="DRAFT")
    started_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    cooldown_until = models.DateTimeField(null=True, blank=True)
    progress = models.JSONField(default=dict, blank=True)  # cached counters
    scores = models.JSONField(default=dict, blank=True)  # per-section + overall
    version = models.CharField(max_length=50, default="v1")

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"Assessment {self.id} - {self.organization.name}"


class Answer(models.Model):
    assessment = models.ForeignKey(Assessment, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="answers")
    data = models.JSONField(default=dict)  # {"value":...} / {"values":[...]} / {"values":{"dim":int}}
    computed_points = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    answered_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = ("assessment", "question")

    def __str__(self):
        return f"Ans: {self.assessment.id} - {self.question.code}"
    
class AssessmentFeedback(models.Model):
    """
    Feedback record per assessment attempt (SPO).
    - 'reasons' are stored as codes (see REASONS below).
    - 'comment' is optional free text.
    """
    class Reason(models.TextChoices):
        HARD_TO_UNDERSTAND = "hard_to_understand", "Questions were difficult to understand"
        TOO_LONG           = "too_long",           "The questionnaire is too long"
        IRRELEVANT         = "irrelevant",         "Questions were irrelevant"
        COME_BACK_LATER    = "come_back_later",    "I will come back and complete it later"
        OTHER              = "other",              "Other"

    assessment = models.OneToOneField(
        "assessments.Assessment",
        on_delete=models.CASCADE,
        related_name="feedback",
    )
    reasons = ArrayField(models.CharField(max_length=64, choices=Reason.choices), default=list, blank=True)
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "assessment_feedback"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Feedback for Assessment {self.assessment_id}"