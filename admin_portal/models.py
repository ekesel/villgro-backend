from django.db import models
from django.conf import settings
from datetime import timedelta

class ActivityLog(models.Model):
    class Action(models.TextChoices):
        CREATE = "CREATE"
        UPDATE = "UPDATE"
        DELETE = "DELETE"
        M2M_ADD = "M2M_ADD"
        M2M_REMOVE = "M2M_REMOVE"
        API_HIT = "API_HIT"
        LOGIN = "LOGIN"
        LOGOUT = "LOGOUT"
        IMPERSONATE = "IMPERSONATE"
        TOGGLE = "TOGGLE"

    # Who did this (nullable for system events)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="activity_logs"
    )

    # What happened
    action = models.CharField(max_length=32, choices=Action.choices)
    app_label = models.CharField(max_length=100, db_index=True)
    model = models.CharField(max_length=100, db_index=True)
    object_id = models.CharField(max_length=64, blank=True, db_index=True)
    object_repr = models.CharField(max_length=255, blank=True)

    # Diffs / context
    changes = models.JSONField(default=dict, blank=True)  # {"field": {"from": "...", "to": "..."}} or {"added": [...]} etc
    meta = models.JSONField(default=dict, blank=True)     # request info, ip, ua, path, method, status, query params

    # Human-readable sentence
    help_text = models.TextField(blank=True)

    # When
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["app_label", "model", "object_id"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        base = f"{self.action} {self.app_label}.{self.model}"
        return f"{base}#{self.object_id} by {self.actor_id} at {self.created_at:%Y-%m-%d %H:%M:%S}"
    

class AdminConfig(models.Model):
    """
    Singleton-ish model to store global admin configuration.
    """

    class CooldownUnit(models.TextChoices):
        MINUTES = "minutes", "Minutes"
        HOURS = "hours", "Hours"
        DAYS = "days", "Days"

    # NEW (use these going forward)
    assessment_cooldown_value = models.PositiveIntegerField(
        default=7,
        help_text="Cooldown value before a startup can begin a new assessment."
    )
    assessment_cooldown_unit = models.CharField(
        max_length=10,
        choices=CooldownUnit.choices,
        default=CooldownUnit.DAYS,
        help_text="Cooldown unit: minutes | hours | days."
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return (
            f"AdminConfig(id={self.pk}, cooldown={self.assessment_cooldown_value} {self.assessment_cooldown_unit})"
        )
    
    def get_assessment_cooldown_timedelta(self):
        value = self.assessment_cooldown_value
        unit = self.assessment_cooldown_unit

        if unit == "minutes":
            return timedelta(minutes=value)
        if unit == "hours":
            return timedelta(hours=value)

        # default = days
        return timedelta(days=value)
    