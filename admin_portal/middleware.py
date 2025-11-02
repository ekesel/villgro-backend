import logging, time
from admin_portal.audit_local import set_current_request, get_actor
from admin_portal.models import ActivityLog
from admin_portal.signals import _db_ready
from django.utils.deprecation import MiddlewareMixin

audit_logger = logging.getLogger("http.audit")

SKIP_PREFIXES = ("/static/", "/media/")
SKIP_PATHS = {"/health", "/readiness", "/liveness"}

class RequestActivityMiddleware:
    """
    - Stores request on threadlocal so signals can see the actor.
    - Logs every API hit as ActivityLog(action=API_HIT).
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        set_current_request(request)
        response = self.get_response(request)

        if not _db_ready():
            return response

        try:
            # Only log DRF/API routes under /api/
            if request.path.startswith("/api/"):
                actor = get_actor()
                meta = {
                    "path": request.path,
                    "method": request.method,
                    "status": getattr(response, "status_code", None),
                    "query": request.META.get("QUERY_STRING", ""),
                }
                # avoid huge payloads; capture small body safely
                try:
                    body = request.body.decode("utf-8")[:2048]
                    meta["body"] = body
                except Exception:
                    pass

                ActivityLog.objects.create(
                    actor=actor,
                    action=ActivityLog.Action.API_HIT,
                    app_label="http",
                    model="Request",
                    object_id="",  # N/A
                    object_repr="",
                    changes={},
                    meta=meta,
                    help_text=f"API {request.method} {request.path} ({meta['status']})"
                )
        except Exception:
            # Never break the request on logging errors
            pass

        return response
    
class RequestResponseLoggingMiddleware(MiddlewareMixin):
    def process_request(self, request):
        request._start_time = time.monotonic()

    def process_response(self, request, response):
        try:
            path = request.path or ""
            if any(path.startswith(p) for p in SKIP_PREFIXES) or path in SKIP_PATHS:
                return response

            dur_ms = None
            if hasattr(request, "_start_time"):
                dur_ms = int((time.monotonic() - request._start_time) * 1000)

            user = getattr(request, "user", None)
            uid = getattr(user, "id", None)
            uemail = getattr(user, "email", None)

            # Basics
            method = request.method
            status = getattr(response, "status_code", None)
            clen = response.get("Content-Length") or "-"
            ip = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() or request.META.get("REMOTE_ADDR", "")
            ua = request.META.get("HTTP_USER_AGENT", "")

            audit_logger.info(
                f'{method} {path} {status} dur_ms={dur_ms} bytes={clen} ip={ip} user_id={uid} user_email="{uemail}" ua="{ua}"'
            )
        finally:
            return response