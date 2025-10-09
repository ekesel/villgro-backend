from admin_portal.models import AuditLog

def audit(actor, action, target, before=None, after=None):
    AuditLog.objects.create(
        actor=actor,
        action=action,
        target_model=target.__class__.__name__,
        target_id=str(getattr(target, "pk", "")),
        changes={"before": before or {}, "after": after or {}},
    )