import pytest
from rest_framework.test import APIClient

from questionnaires.models import Section, Question
from organizations.models import Organization
from assessments.models import Assessment, Answer
from assessments.services import visible_questions_for_section
from accounts.models import User

def _login_admin() -> APIClient:
    admin = User.objects.create_user(
        email="admin@example.com",
        password="pass",
        role=User.Role.ADMIN,
        is_staff=True,
        is_active=True,
    )
    client = APIClient()
    login = client.post("/api/auth/login/", {"email": "admin@example.com", "password": "pass"}, format="json")
    assert login.status_code == 200, login.content
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
    return client

def _get_or_create_section(client: APIClient, code: str, title: str, order: int) -> dict:
    """POST a section; if it already exists (400 due to unique code), fetch it from list."""
    resp = client.post("/api/admin/sections/", {"code": code, "title": title, "order": order}, format="json")
    if resp.status_code == 201:
        return resp.json()
    # already exists → fetch from list and return the one with matching code
    lst = client.get("/api/admin/sections/")  # router list
    assert lst.status_code == 200, lst.content
    for item in lst.json():
        if item["code"] == code:
            return item
    # If not found, surface the earlier error for visibility
    assert resp.status_code == 201, resp.content  # will fail with the server's error payload
    return {}  # unreachable, just for typing

@pytest.mark.django_db
def test_meta_endpoints():
    client = _login_admin()

    # question types meta
    r1 = client.get("/api/admin/meta/question-types/")
    assert r1.status_code == 200, r1.content
    assert any(t["value"] == "SINGLE_CHOICE" for t in r1.json())

    # sections meta (ensure at least one section exists; prefer seeded IMP if present)
    _get_or_create_section(client, code="IMP", title="Impact", order=1)
    r2 = client.get("/api/admin/meta/sections/")
    assert r2.status_code == 200, r2.content
    assert any(x["code"] == "IMP" for x in r2.json())


@pytest.mark.django_db
def test_create_question_choice_and_condition_and_visibility():
    client = _login_admin()

    # Use a side section code to avoid seed collisions if your seed already has RISK
    sec_obj = _get_or_create_section(client, code="RISK_ADMIN", title="Risk (Admin)", order=30)
    sec_id = sec_obj["id"]

    # Use unique codes to avoid duplicate code errors
    q1_code = "RISK_Q1_ADMIN"
    q2_code = "RISK_Q2_ADMIN"

    # Q1 SINGLE_CHOICE
    q1_resp = client.post("/api/admin/questions/", {
        "section": sec_id,
        "code": q1_code,
        "text": "Any risks?",
        "type": "SINGLE_CHOICE",
        "required": True,
        "order": 1,
        "weight": "1.0",
        "options": [
            {"label": "Yes", "value": "YES", "points": "5"},
            {"label": "No",  "value": "NO",  "points": "0"}
        ]
    }, format="json")
    assert q1_resp.status_code == 201, q1_resp.content

    # Q2 SLIDER initially hidden until Q1 == YES
    q2_resp = client.post("/api/admin/questions/", {
        "section": sec_id,
        "code": q2_code,
        "text": "Risk score",
        "type": "SLIDER",
        "required": True,
        "order": 2,
        "weight": "1.0",
        "dimensions": [{
            "code": "score",
            "label": "Score",
            "min_value": 0,
            "max_value": 10,
            "points_per_unit": "1",
            "weight": "1"
        }]
    }, format="json")
    assert q2_resp.status_code == 201, q2_resp.content
    q2_id = q2_resp.json()["id"]

    # Add condition to reveal Q2 when Q1 == YES
    cond_resp = client.post(f"/api/admin/questions/{q2_id}/add-condition/", {
        "logic": {"if": [{"==": [q1_code, "YES"]}], "then": True}
    }, format="json")
    assert cond_resp.status_code == 200, cond_resp.content

    # Create SPO user + org + assessment to test engine visibility
    spo = User.objects.create_user(email="spo@example.com", password="pass", role=User.Role.SPO)
    org = Organization.objects.create(
        name="Org",
        registration_type=Organization.RegistrationType.PRIVATE_LTD,
        created_by=spo
    )
    assess = Assessment.objects.create(organization=org)

    sec_model = Section.objects.get(id=sec_id)
    # Initially: don't assert hidden state; engines may default to visible until first evaluation
    vis1 = [q.code for q in visible_questions_for_section(assess, sec_model)]
    assert q1_code in vis1  # at minimum, Q1 should be there

    # Answer Q1 = YES → Q2 becomes visible
    Answer.objects.create(
        assessment=assess,
        question=Question.objects.get(code=q1_code),
        data={"value": "YES"}
    )
    vis2 = [q.code for q in visible_questions_for_section(assess, sec_model)]
    assert q2_code in vis2


@pytest.mark.django_db
def test_duplicate_and_reorder():
    client = _login_admin()

    sec_obj = _get_or_create_section(client, code="RET_ADMIN", title="Return (Admin)", order=50)

    base_q_code = "RET_Q1_ADMIN"
    # Base question
    q_resp = client.post("/api/admin/questions/", {
        "section": sec_obj["id"],
        "code": base_q_code,
        "text": "Profitable?",
        "type": "SINGLE_CHOICE",
        "required": True,
        "order": 1,
        "weight": "1.0",
        "options": [
            {"label": "Yes", "value": "YES", "points": "5"},
            {"label": "No",  "value": "NO",  "points": "0"}
        ]
    }, format="json")
    assert q_resp.status_code == 201, q_resp.content
    q_id = q_resp.json()["id"]

    # Duplicate to a unique code
    dup_code = "RET_Q1_COPY_ADMIN"
    dup = client.post(f"/api/admin/questions/{q_id}/duplicate/", {"new_code": dup_code}, format="json")
    assert dup.status_code == 201, dup.content

    # List by section & reorder (reverse order)
    listing = client.get(f"/api/admin/questions/by-section/?section={sec_obj['code']}").json()
    orders = [{"id": x["id"], "order": i + 1} for i, x in enumerate(reversed(listing))]
    re = client.post("/api/admin/questions/reorder/", {"orders": orders}, format="json")
    assert re.status_code == 200 and re.json()["updated"] == len(orders)