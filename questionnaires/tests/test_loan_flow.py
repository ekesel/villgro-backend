# questionnaires/tests/test_loan_flow_api.py
import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from organizations.models import Organization
from assessments.models import Assessment
from questionnaires.models import Section, EligibilityRule


@pytest.fixture
def seed_sections_and_rules(db):
    impact = Section.objects.get_or_create(code="IMPACT", defaults={"title": "Impact", "order": 1})[0]
    risk   = Section.objects.get_or_create(code="RISK",   defaults={"title": "Risk",   "order": 2})[0]
    ret    = Section.objects.get_or_create(code="RETURN", defaults={"title": "Return", "order": 3})[0]
    EligibilityRule.objects.update_or_create(section=impact, defaults={"min_threshold": 60, "max_threshold": 100, "weight": 40})
    EligibilityRule.objects.update_or_create(section=risk,   defaults={"min_threshold":  0, "max_threshold":  40, "weight": 30})
    EligibilityRule.objects.update_or_create(section=ret,    defaults={"min_threshold": 50, "max_threshold": 100, "weight": 30})


def auth_client(email="spo@x.com", password="Pass123!", role="SPO"):
    user = User.objects.create_user(email=email, password=password, role=role)
    c = APIClient()
    tok = c.post("/api/auth/login/", {"email": email, "password": password}, format="json").data["access"]
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {tok}")
    return user, c


@pytest.mark.django_db
def test_meta_and_prefill(seed_sections_and_rules):
    u, c = auth_client()
    org = Organization.objects.create(
        name="Org A",
        registration_type=Organization.RegistrationType.PRIVATE_LTD,
        created_by=u,
    )
    a = Assessment.objects.create(
        organization=org,
        status="SUBMITTED",
        submitted_at=timezone.now(),
        scores={"sections": {"IMPACT": 90, "RISK": 20, "RETURN": 90}, "overall": 0},
    )

    # /api/loan/meta/
    r_meta = c.get("/api/loan/meta/")
    assert r_meta.status_code == 200
    meta = r_meta.json()
    assert "fund_types" in meta and isinstance(meta["fund_types"], list)

    # /api/loan/prefill/?assessment_id=...
    r_pf = c.get(f"/api/loan/prefill/?assessment_id={a.id}")
    assert r_pf.status_code == 200
    body = r_pf.json()
    assert body["assessment_id"] == a.id
    assert "organization" in body and isinstance(body["organization"], dict)
    assert "assessment_scores" in body


@pytest.mark.django_db
def test_check_eligibility_then_submit(seed_sections_and_rules):
    u, c = auth_client()
    org = Organization.objects.create(
        name="Org B",
        registration_type=Organization.RegistrationType.PRIVATE_LTD,
        created_by=u,
    )
    a = Assessment.objects.create(
        organization=org,
        status="SUBMITTED",
        submitted_at=timezone.now(),
        scores={"sections": {"IMPACT": 100, "RISK": 10, "RETURN": 95}, "overall": 0},
    )

    # /api/loan/eligibility/?assessment_id=...
    r_elig = c.get(f"/api/loan/eligibility/?assessment_id={a.id}")
    assert r_elig.status_code == 200
    assert r_elig.json()["is_eligible"] is True

    # POST /api/loan/
    payload = {
        "assessment": a.id,
        "founder_name": "A Founder",
        "founder_email": "founder@x.com",
        "amount_in_inr": "5000000.00",
        "fund_type": "GROWTH_CAPITAL",
    }
    r_create = c.post("/api/loan/", payload, format="json")
    assert r_create.status_code == 201, r_create.content
    data = r_create.json()
    assert data["assessment"] == a.id
    assert data["organization"] == org.id
    assert data["eligibility_decision"] is True
    assert data["status"] == "SUBMITTED"