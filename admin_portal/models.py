from django.db import models
from django.conf import settings

from django.db import models
from django.conf import settings

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
    For now, only assessment_cooldown_days is stored, but
    more fields can be added later.
    """
    assessment_cooldown_days = models.PositiveIntegerField(
        default=7,
        help_text="Cooldown in days before a startup can begin a new assessment."
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def get_solo(cls):
        """
        Ensure there is always exactly one config row.
        """
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return f"AdminConfig(id={self.pk}, cooldown={self.assessment_cooldown_days} days)"