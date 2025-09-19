from django.db import models
from django.utils import timezone
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