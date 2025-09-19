from django.db import models

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

    class Meta:
        ordering = ["section", "order"]

    def __str__(self):
        return f"{self.section.code} - {self.code}"


class AnswerOption(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="options")
    label = models.CharField(max_length=255)
    value = models.CharField(max_length=100)  # frontend key
    points = models.DecimalField(max_digits=6, decimal_places=2, default=0)

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