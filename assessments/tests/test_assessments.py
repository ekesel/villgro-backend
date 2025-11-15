import pytest
import uuid
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from assessments.models import Assessment, Answer
from questionnaires.models import Section, Question
from organizations.models import Organization

pytestmark = pytest.mark.django_db


def test_start_and_current_assessment(api_client):
    client, user = api_client

    url = reverse("assessment-start")
    resp = client.post(url)
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "DRAFT"

    # current should return the same draft
    url = reverse("assessment-current")
    resp2 = client.get(url)
    assert resp2.status_code == 200
    assert resp2.json()["id"] == data["id"]


def test_sections_list_with_progress(api_client):
    client, user = api_client
    assessment = Assessment.objects.create(organization=user.organization)

    url = reverse("assessment-sections", args=[assessment.id])
    resp = client.get(url)
    assert resp.status_code == 200
    data = resp.json()
    assert "sections" in data
    assert any(s["code"] == "IMPACT" for s in data["sections"])


def test_questions_visibility_with_branching(api_client):
    client, user = api_client
    assessment = Assessment.objects.create(organization=user.organization)

    # Initially, IMP_Q2 hidden because condition IMP_Q1==YES not met
    url = reverse("assessment-questions", args=[assessment.id])
    resp = client.get(url, {"section": "IMPACT"})
    q_codes = [q["code"] for q in resp.json()["questions"]]
    assert "IMP_Q1" in q_codes
    assert "IMP_Q2" not in q_codes

    # Save answer YES for IMP_Q1
    ans_url = reverse("assessment-save-answers", args=[assessment.id])
    payload = {"answers": [{"question": "IMP_Q1", "data": {"value": "YES"}}]}
    client.patch(ans_url, payload, format="json")

    # Now IMP_Q2 should be visible
    resp2 = client.get(url, {"section": "IMPACT"})
    q_codes2 = [q["code"] for q in resp2.json()["questions"]]
    assert "IMP_Q2" in q_codes2


def test_save_answers_and_progress(api_client):
    client, user = api_client
    assessment = Assessment.objects.create(organization=user.organization)

    ans_url = reverse("assessment-save-answers", args=[assessment.id])
    payload = {
        "answers": [
            {"question": "IMP_Q1", "data": {"value": "YES"}},
            {"question": "RISK_Q1", "data": {"values": ["OP", "FIN"]}},
        ]
    }
    resp = client.patch(ans_url, payload, format="json")
    assert resp.status_code == 200
    progress = resp.json()["progress"]
    assert progress["answered"] >= 2

    # Answers actually stored
    assert Answer.objects.filter(assessment=assessment).count() == 2


def test_submit_and_scoring(api_client):
    client, user = api_client
    assessment = Assessment.objects.create(organization=user.organization)

    # Answer required visible questions
    ans_url = reverse("assessment-save-answers", args=[assessment.id])
    payload = {
        "answers": [
            {"question": "IMP_Q1", "data": {"value": "YES"}},
            {"question": "IMP_Q2", "data": {"values": {"reach": 5, "depth": 7}}},
            {"question": "RISK_Q1", "data": {"values": ["OP"]}},
            {"question": "RET_Q1", "data": {"value": 6}},
            {"question": "SEC_Q1", "data": {"value": 4}},
        ]
    }
    client.patch(ans_url, payload, format="json")

    # Submit
    url = reverse("assessment-submit", args=[assessment.id])
    resp = client.post(url)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "SUBMITTED"
    assert "scores" in data
    assert data["scores"]["overall"] > 0


def test_cooldown_prevents_new_attempt(api_client):
    client, user = api_client

    # create a submitted one with cooldown_until in future
    Assessment.objects.create(
        organization=user.organization,
        status="SUBMITTED",
        submitted_at=timezone.now(),
        cooldown_until=timezone.now() + timezone.timedelta(days=30),
    )

    url = reverse("assessment-start")
    resp = client.post(url)
    assert resp.status_code == 403


def test_history_and_results(api_client):
    client, user = api_client
    a = Assessment.objects.create(
        organization=user.organization,
        status="SUBMITTED",
        submitted_at=timezone.now(),
        scores={"sections": {"IMPACT": 7.0}, "overall": 7.0},
    )

    # history
    url = reverse("assessment-history")
    resp = client.get(url)
    assert resp.status_code == 200
    assert resp.json()[0]["id"] == a.id

    # results (must be SUBMITTED + belong to user.org)
    url = reverse("assessment-results", args=[a.id])
    resp = client.get(url)
    assert resp.status_code == 200
    assert resp.json()["scores"]["overall"] == 7.0


def test_submit_with_missing_required_answers(api_client):
    client, user = api_client
    assessment = Assessment.objects.create(organization=user.organization)

    # Answer only one required question
    ans_url = reverse("assessment-save-answers", args=[assessment.id])
    payload = {"answers": [{"question": "IMP_Q1", "data": {"value": "YES"}}]}
    client.patch(ans_url, payload, format="json")

    # Try submitting
    url = reverse("assessment-submit", args=[assessment.id])
    resp = client.post(url)
    assert resp.status_code == 400
    data = resp.json()
    assert data["message"] == "Missing answers"


def test_save_answers_with_invalid_question(api_client):
    client, user = api_client
    assessment = Assessment.objects.create(organization=user.organization)

    ans_url = reverse("assessment-save-answers", args=[assessment.id])
    payload = {"answers": [{"question": "INVALID_Q", "data": {"value": "YES"}}]}
    resp = client.patch(ans_url, payload, format="json")
    # Should fail because the question doesn't exist
    assert resp.status_code == 400
    assert "Invalid question code" in resp.json()["message"]


def test_cannot_submit_twice(api_client):
    client, user = api_client
    assessment = Assessment.objects.create(organization=user.organization)

    # Fill required answers
    ans_url = reverse("assessment-save-answers", args=[assessment.id])
    payload = {
        "answers": [
            {"question": "IMP_Q1", "data": {"value": "YES"}},
            {"question": "IMP_Q2", "data": {"values": {"reach": 5, "depth": 7}}},
            {"question": "RISK_Q1", "data": {"values": ["OP"]}},
            {"question": "RET_Q1", "data": {"value": 6}},
            {"question": "SEC_Q1", "data": {"value": 4}},
        ]
    }
    client.patch(ans_url, payload, format="json")

    url = reverse("assessment-submit", args=[assessment.id])

    # First submit should succeed
    resp1 = client.post(url)
    assert resp1.status_code == 200

    # Second submit should 404 (no longer a DRAFT)
    resp2 = client.post(url)
    assert resp2.status_code == 404


def test_start_fails_during_cooldown(api_client):
    client, user = api_client

    Assessment.objects.create(
        organization=user.organization,
        status="SUBMITTED",
        submitted_at=timezone.now(),
        cooldown_until=timezone.now() + timezone.timedelta(days=180),
    )

    url = reverse("assessment-start")
    resp = client.post(url)
    assert resp.status_code == 403
    assert "Next attempt available" in resp.json()["message"]


def test_user_cannot_access_other_org_assessment(api_client, django_user_model):
    client, user = api_client

    # unique email per run
    unique_email = f"other-{uuid.uuid4().hex[:8]}@example.com"
    other_user = django_user_model.objects.create_user(email=unique_email, password="pass")

    other_org = Organization.objects.create(
        name="Other Org",
        registration_type=Organization.RegistrationType.PRIVATE_LTD,
        created_by=other_user,
    )
    other_assessment = Assessment.objects.create(organization=other_org)

    url = reverse("assessment-results", args=[other_assessment.id])
    resp = client.get(url)
    # must not access → should be 404
    assert resp.status_code == 404

def test_slider_question_has_min_max(api_client):
    client, user = api_client
    assessment = Assessment.objects.create(organization=user.organization)
    url = reverse("assessment-questions", args=[assessment.id])
    resp = client.get(url, {"section": "RETURN"})
    data = resp.json()
    q = next(q for q in data["questions"] if q["type"] == "SLIDER")
    assert "min" in q and "max" in q and "step" in q

def test_optional_feedback_section(api_client):
    client, user = api_client
    assessment = Assessment.objects.create(organization=user.organization)

    # Fetch feedback questions
    url = reverse("assessment-questions", args=[assessment.id])
    resp = client.get(url, {"section": "FEEDBACK"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["section"] == "FEEDBACK"
    assert any(q["code"] == "FB_Q1" for q in data["questions"])

    # Save feedback answers
    ans_url = reverse("assessment-save-answers", args=[assessment.id])
    payload = {
        "answers": [
            {"question": "FB_Q1", "data": {"value": "LATER"}},
            {"question": "FB_Q2", "data": {"value": 4}}
        ]
    }
    resp2 = client.patch(ans_url, payload, format="json")
    assert resp2.status_code == 200

    # Answers stored in DB
    assert Answer.objects.filter(assessment=assessment, question__code="FB_Q1").exists()
    assert Answer.objects.filter(assessment=assessment, question__code="FB_Q2").exists()

    # User can still resume draft normally
    current_url = reverse("assessment-current")
    resp3 = client.get(current_url)
    assert resp3.status_code == 200
    assert resp3.json()["status"] == "DRAFT"

def test_feedback_can_be_skipped(api_client):
    client, user = api_client
    assessment = Assessment.objects.create(organization=user.organization)

    # Don’t answer feedback at all → just resume
    url = reverse("assessment-current")
    resp = client.get(url)
    assert resp.status_code == 200
    assert resp.json()["status"] == "DRAFT"

    # Submit without feedback
    # (simulate answering minimum required from other sections)
    ans_url = reverse("assessment-save-answers", args=[assessment.id])
    payload = {
        "answers": [
            {"question": "IMP_Q1", "data": {"value": "YES"}},
            {"question": "IMP_Q2", "data": {"values": {"reach": 5, "depth": 7}}},
            {"question": "RISK_Q1", "data": {"values": ["OP"]}},
            {"question": "RET_Q1", "data": {"value": 4}},
            {"question": "SEC_Q1", "data": {"value": 3}}
        ]
    }
    client.patch(ans_url, payload, format="json")

    submit_url = reverse("assessment-submit", args=[assessment.id])
    resp2 = client.post(submit_url)
    assert resp2.status_code == 200
    data = resp2.json()
    assert data["status"] == "SUBMITTED"

    # Feedback section is optional → submit works even if unanswered
    assert "FEEDBACK" not in data["scores"]["sections"]


def test_progress_percent_and_resume(api_client):
    client, user = api_client
    assessment = Assessment.objects.create(organization=user.organization)

    # Start percent should be 0
    url = reverse("assessment-sections", args=[assessment.id])
    resp = client.get(url)
    assert resp.status_code == 200
    data = resp.json()
    assert "percent" in data["progress"]
    assert data["progress"]["percent"] == 0
    assert data["resume"]["last_section"] is None

    # Save an answer in IMPACT section
    ans_url = reverse("assessment-save-answers", args=[assessment.id])
    payload = {"answers": [{"question": "IMP_Q1", "data": {"value": "YES"}}]}
    resp2 = client.patch(ans_url + "?section=IMPACT", payload, format="json")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert "percent" in data2["progress"]
    assert data2["progress"]["percent"] >= 1
    assert data2["resume"]["last_section"] == "IMPACT"

    # Save in RISK section, should update last_section
    payload2 = {"answers": [{"question": "RISK_Q1", "data": {"values": ["OP"]}}]}
    resp3 = client.patch(ans_url + "?section=RISK", payload2, format="json")
    assert resp3.status_code == 200
    data3 = resp3.json()
    assert data3["resume"]["last_section"] == "RISK"

    # Fill all required answers (simulate full completion)
    payload_full = {
        "answers": [
            {"question": "IMP_Q1", "data": {"value": "YES"}},
            {"question": "IMP_Q2", "data": {"values": {"reach": 5, "depth": 7}}},
            {"question": "RISK_Q1", "data": {"values": ["OP"]}},
            {"question": "RET_Q1", "data": {"value": 4}},
            {"question": "SEC_Q1", "data": {"value": 3}}
        ]
    }
    client.patch(ans_url + "?section=IMPACT", payload_full, format="json")

    # Submit — should succeed and change status to SUBMITTED
    submit_url = reverse("assessment-submit", args=[assessment.id])
    resp_submit = client.post(submit_url)
    assert resp_submit.status_code == 200
    assert resp_submit.json()["status"] == "SUBMITTED"

    # Once submitted, further saves should be blocked
    resp4 = client.patch(ans_url, payload, format="json")
    assert resp4.status_code in [400, 404]
    assert "cannot be modified" in resp4.json()["message"]

def test_results_summary_endpoint(api_client):
    client, user = api_client
    a = Assessment.objects.create(
        organization=user.organization,
        status="SUBMITTED",
        scores={"sections": {"IMPACT": 7.0, "RISK": 5.5}, "overall": 6.25}
    )
    url = reverse("assessment-results-summary", args=[a.id])
    r = client.get(url)
    assert r.status_code == 200
    data = r.json()
    assert data["overall"] == 6.25
    assert any(s["code"] == "IMPACT" for s in data["sections"])

def test_pdf_report_endpoint(api_client):
    client, user = api_client
    a = Assessment.objects.create(
        organization=user.organization,
        status="SUBMITTED",
        scores={"sections": {"IMPACT": 7.0}, "overall": 7.0}
    )
    url = reverse("assessment-report-pdf", args=[a.id])
    r = client.get(url)
    assert r.status_code == 200
    # Accept either PDF or HTML fallback
    assert r["Content-Type"] in ["application/pdf", "text/html"]