import json
from admin_portal.audit_local import set_current_request, get_actor
from admin_portal.models import ActivityLog
from admin_portal.signals import _db_ready

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