import logging

from django.db.models.signals import pre_save, post_save, post_delete, m2m_changed, post_migrate
from django.dispatch import receiver
from django.db import models

from admin_portal.audit_local import get_actor, get_current_request
from admin_portal.models import ActivityLog
from django.db import connection
from django.conf import settings
from django.db.utils import OperationalError, ProgrammingError
from django.utils.functional import Promise

logger = logging.getLogger(__name__)


AUDIT_READY = False  # flipped to True after all migrations
SENSITIVE_FIELDS = {"password", "reset_token", "otp", "secret", "api_key"}

# ---------------- utilities ----------------

from accounts.models import User

def _safe_actor():
    actor = get_actor()
    try:
        # If actor is a User-like object but no longer exists in DB, drop it
        if actor and isinstance(actor, User):
            if not User.objects.filter(pk=actor.pk).exists():
                return None
        return actor
    except Exception:
        logger.exception("Failed to resolve safe actor")
        return None

def _table_exists(table: str) -> bool:
    try:
        with connection.cursor() as cur:
            cur.execute("""
                SELECT 1
                FROM information_schema.tables
                WHERE table_name = %s
                LIMIT 1
            """, [table])
            return cur.fetchone() is not None
    except (OperationalError, ProgrammingError):
        return False

def _db_ready() -> bool:
    try:
        if not _table_exists("django_content_type"):
            return False
        if not _table_exists("django_migrations"):
            return False
        if not _table_exists("admin_portal_activitylog"):
            return False

        # Do NOT hard-require jti_hex; different simplejwt versions/migration timing
        # vary. Only ensure table presence if the app is installed.
        if "rest_framework_simplejwt.token_blacklist" in settings.INSTALLED_APPS:
            if not _table_exists("token_blacklist_outstandingtoken"):
                return False

        return True
    except Exception:
        logger.exception("Database readiness check failed")
        return False

def _should_log_sender(sender) -> bool:
    try:
        app_label = sender._meta.app_label
    except Exception:
        logger.exception("Failed to resolve app label for sender %s", sender)
        return False
    # Skip Django/framework/system apps
    if app_label in {
        "admin", "auth", "contenttypes", "sessions",
        "messages", "staticfiles", "authtoken", "rest_framework",
        "rest_framework_simplejwt", "token_blacklist", "corsheaders", "django_filters",
    }:
        return False
    # Skip our own log model
    from admin_portal.models import ActivityLog
    if sender is ActivityLog:
        return False
    return True

def _model_meta(instance):
    opts = instance._meta
    return opts.app_label, opts.model_name

def _short_repr(instance):
    # try get a nice representation
    s = str(instance)
    return s[:255]

def _field_verbose(instance, field_name):
    try:
        v = instance._meta.get_field(field_name).verbose_name or field_name
        return str(v)  # <-- ensure no __proxy__ leaks into JSON
    except Exception:
        logger.exception("Failed to resolve verbose name for field %s on %s", field_name, instance)
        return field_name
    
def _json_safe(value):
    import datetime, decimal, uuid
    if isinstance(value, Promise):
        return str(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    return value

def _serialize_value(instance, field_name, value):
    # human readable – for FKs, show string not PK
    try:
        field = instance._meta.get_field(field_name)
        if isinstance(field, (models.ForeignKey, models.OneToOneField)):
            return _json_safe(str(value) if value is not None else None)
    except Exception:
        logger.exception("Failed to serialize field %s on %s", field_name, instance)
        pass
    return _json_safe(value)

# We keep a pre_save snapshot on the instance so post_save can diff
_PREVIOUS_STATE_ATTR = "__audit_prev_state__"

def _snapshot(instance):
    data = {}
    for f in instance._meta.get_fields():
        if getattr(f, "many_to_many", False) or f.many_to_one and f.related_model is None:
            continue
        if hasattr(f, "attname"):  # only concrete fields
            name = f.name
            try:
                val = getattr(instance, name)
                data[name] = _serialize_value(instance, name, val)
            except Exception:
                logger.exception("Failed to snapshot field %s on %s", name, instance)
                pass
    return data

# ---------------- handlers ----------------
@receiver(pre_save)
def capture_pre_save(sender, instance, **kwargs):
    if not (_should_log_sender(sender)):
        return
    # ignore our own model
    if sender is ActivityLog: return
    try:
        if instance.pk:
            # load DB state for diffs
            old = sender.objects.filter(pk=instance.pk).first()
            if old:
                setattr(instance, _PREVIOUS_STATE_ATTR, _snapshot(old))
    except Exception:
        logger.exception("Failed to capture pre-save snapshot for %s", instance)
        pass

@receiver(post_save)
def log_post_save(sender, instance, created, **kwargs):
    logger.info("post_save signal received for %s (created=%s)", instance, created)
    if not (_should_log_sender(sender)):
        logger.info("Audit not ready or DB not ready or sender not to be logged")
        return
    if sender is ActivityLog: return

    try:
        app_label, model = _model_meta(instance)
        actor = _safe_actor()
        req = get_current_request()

        now_state = _snapshot(instance)
        prev_state = getattr(instance, _PREVIOUS_STATE_ATTR, None)

        if created:
            # CREATE: store non-null values in 'to'
            changes = {
                k: {"from": None, "to": v} for k, v in now_state.items() if v not in [None, "", []]
            }
            help_text = f"Created {app_label}.{model} “{_short_repr(instance)}”."
            ActivityLog.objects.create(
                actor=actor,
                action=ActivityLog.Action.CREATE,
                app_label=app_label, model=model,
                object_id=str(instance.pk),
                object_repr=_short_repr(instance),
                changes=changes,
                meta=_request_meta(req),
                help_text=help_text,
            )
        else:
            # UPDATE: diff prev vs now
            diffs = {}
            if prev_state is None:
                prev_state = {}
            for field, new_val in now_state.items():
                old_val = prev_state.get(field)
                if new_val != old_val:
                    if field in SENSITIVE_FIELDS:
                        diffs[field] = {"from": "***", "to": "***", "label": _field_verbose(instance, field)}
                    else:
                        diffs[field] = {
                            "from": old_val,
                            "to": new_val,
                            "label": _field_verbose(instance, field),
                        }
            if diffs:
                msgs = []
                for f, d in diffs.items():
                    label = d.get("label") or f
                    msgs.append(f"{label} changed from “{d['from']}” to “{d['to']}”")
                help_text = f"Updated {app_label}.{model} “{_short_repr(instance)}”: " + "; ".join(msgs)
                ActivityLog.objects.create(
                    actor=actor,
                    action=ActivityLog.Action.UPDATE,
                    app_label=app_label, model=model,
                    object_id=str(instance.pk),
                    object_repr=_short_repr(instance),
                    changes=diffs,
                    meta=_request_meta(req),
                    help_text=help_text,
                )
    except Exception:
        logger.exception("Failed to log post-save activity for %s", instance)
        pass
    finally:
        # cleanup snapshot to avoid leaking on instance
        if hasattr(instance, _PREVIOUS_STATE_ATTR):
            delattr(instance, _PREVIOUS_STATE_ATTR)

@receiver(post_delete)
def log_post_delete(sender, instance, **kwargs):
    if not (_should_log_sender(sender)):
        return
    if sender is ActivityLog: return

    # --- skip deleting User to avoid teardown FK issues ---
    try:
        if sender is User:
            return
    except Exception:
        logger.exception("Failed to determine if actor matches deleted user")
        pass

    try:
        app_label, model = _model_meta(instance)
        actor = _safe_actor()
        # If the current actor is the same object being deleted, null it out
        try:
            if actor and isinstance(actor, User) and sender is User and actor.pk == getattr(instance, "pk", None):
                actor = None
        except Exception:
            actor = None
            
        req = get_current_request()
        ActivityLog.objects.create(
            actor=actor,
            action=ActivityLog.Action.DELETE,
            app_label=app_label, model=model,
            object_id=str(instance.pk),
            object_repr=_short_repr(instance),
            changes={},
            meta=_request_meta(req),
            help_text=f"Deleted {app_label}.{model} “{_short_repr(instance)}”.",
        )
    except Exception:
        logger.exception("Failed to log post-delete activity for %s", instance)
        pass

@receiver(m2m_changed)
def log_m2m(sender, instance, action, reverse, model, pk_set, **kwargs):
    if not (_should_log_sender(sender)):
        return
    try:
        # Only log add/remove (not pre/post clear separately)
        if action not in ("post_add", "post_remove"):
            return
        app_label, model_name = _model_meta(instance)
        actor = _safe_actor()
        req = get_current_request()
        act = ActivityLog.Action.M2M_ADD if action == "post_add" else ActivityLog.Action.M2M_REMOVE

        # Try to render related objects nicely
        try:
            added = list(model.objects.filter(pk__in=pk_set))
            labels = [str(o) for o in added]
        except Exception:
            labels = [str(pk) for pk in pk_set]

        verbs = "added" if act == ActivityLog.Action.M2M_ADD else "removed"
        help_text = f"{verbs} {len(pk_set)} related item(s) on {app_label}.{model_name} “{_short_repr(instance)}”: {', '.join(labels[:5])}"

        ActivityLog.objects.create(
            actor=actor,
            action=act,
            app_label=app_label, model=model_name,
            object_id=str(instance.pk),
            object_repr=_short_repr(instance),
            changes={"related": labels, "count": len(pk_set)},
            meta=_request_meta(req),
            help_text=help_text,
        )
    except Exception:
        logger.exception("Failed to log m2m activity for %s", instance)
        pass

def _request_meta(req):
    if not req:
        return {}
    meta = {
        "ip": req.META.get("REMOTE_ADDR"),
        "ua": req.META.get("HTTP_USER_AGENT"),
        "path": req.path,
        "method": req.method,
    }
    return meta

@receiver(post_migrate, dispatch_uid="admin_portal_enable_audit_after_migrate")
def _enable_audit_after_migrate(**kwargs):
    global AUDIT_READY
    AUDIT_READY = True