import logging
from typing import Iterable, Mapping, Any, Optional
from django.core.mail import send_mail, EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings

logger = logging.getLogger("notifications")

# Event keys we'll support now
# ASSESSMENT_SUBMITTED = "assessment_submitted"
# LOAN_REQUEST_SUBMITTED = "loan_request_submitted"
# SPO_FEEDBACK_SUBMITTED = "spo_feedback_submitted"
ASSESSMENT_LEFT_IN_MIDDLE = "assessment_left_in_middle"

# Map event -> subject, templates (txt/html)
EVENT_TEMPLATES: dict[str, dict[str, str]] = {
    # ASSESSMENT_SUBMITTED: {
    #     "subject": "Assessment submitted: #{assessment.id} – {org.name}",
    #     "text": "emails/assessment_submitted.txt",
    #     "html": "emails/assessment_submitted.html",
    # },
    # LOAN_REQUEST_SUBMITTED: {
    #     "subject": "Loan request submitted: #{loan.id} – {org.name}",
    #     "text": "emails/loan_request_submitted.txt",
    #     "html": "emails/loan_request_submitted.html",
    # },
    # SPO_FEEDBACK_SUBMITTED: {
    #     "subject": "New SPO feedback for assessment #{assessment.id} – {org.name}",
    #     "text": "emails/spo_feedback_submitted.txt",
    #     "html": "emails/spo_feedback_submitted.html",
    # },
}

def _format_subject(template: str, ctx: Mapping[str, Any]) -> str:
    # Safe-ish format: nested lookups allowed via dotted access in templates,
    # here we only use flat {} with dicts inside context.
    try:
        return template.format(**ctx)  # keep simple for now
    except Exception:
        logger.exception("Failed to format email subject with ctx=%s", ctx)
        return template

def notify_email(
    *,
    event: str,
    to: Iterable[str],
    context: Mapping[str, Any],
    cc: Optional[Iterable[str]] = None,
    bcc: Optional[Iterable[str]] = None,
    reply_to: Optional[Iterable[str]] = None,
    from_email: Optional[str] = None,
) -> bool:
    """
    Render and send an email for `event` with the given context.
    Returns True if at least one message was accepted for delivery.
    """
    meta = EVENT_TEMPLATES.get(event)
    if not meta:
        logger.warning("Email event '%s' not configured; skipping.", event)
        return False

    from_email = from_email or settings.DEFAULT_FROM_EMAIL
    txt_tmpl = meta["text"]
    html_tmpl = meta.get("html")
    subject = _format_subject(meta["subject"], context)

    try:
        body_txt = render_to_string(txt_tmpl, context)
        body_html = render_to_string(html_tmpl, context) if html_tmpl else None

        if body_html:
            msg = EmailMultiAlternatives(
                subject=subject,
                body=body_txt,
                from_email=from_email,
                to=list(to),
                cc=list(cc or []),
                bcc=list(bcc or []),
                reply_to=list(reply_to or []),
            )
            msg.attach_alternative(body_html, "text/html")
            sent = msg.send(fail_silently=False)
        else:
            sent = send_mail(
                subject=subject,
                message=body_txt,
                from_email=from_email,
                recipient_list=list(to),
                fail_silently=False,
            )

        logger.info("Email event=%s sent=%s to=%s", event, bool(sent), list(to))
        return bool(sent)
    except Exception:
        logger.exception("Email send failed: event=%s to=%s", event, list(to))
        return False
    
def send_spo_abandoned_email(*, spo, org, assessment, recorded_at) -> bool:
    """
    Sends a single email to the SPO notifying them that progress was saved
    and they can resume the assessment.
    Returns True if Django reports a successful send (>=1), else False.
    """
    if not getattr(spo, "email", None):
        logger.warning("SPO has no email; skipping abandoned notice. assessment=%s", assessment.id)
        return False

    ctx = {
        "spo": spo,
        "org": org,
        "assessment": assessment,
        "recorded_at": recorded_at,
    }

    subject = render_to_string("emails/spo_abandoned_subject.txt", ctx).strip()
    body_txt = render_to_string("emails/spo_abandoned.txt", ctx)
    body_html = render_to_string("emails/spo_abandoned.html", ctx)

    email = EmailMultiAlternatives(
        subject=subject,
        body=body_txt,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        to=[spo.email],
    )
    email.attach_alternative(body_html, "text/html")

    try:
        sent = email.send()
        logger.info("Sent SPO abandoned email to %s for assessment=%s (sent=%s)", spo.email, assessment.id, sent)
        return bool(sent)
    except Exception:
        logger.exception("Failed to send SPO abandoned email for assessment=%s", assessment.id)
        return False