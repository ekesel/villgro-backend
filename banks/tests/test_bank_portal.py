# banks/tests/test_bank_portal.py
import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from organizations.models import Organization
from assessments.models import Assessment
from questionnaires.models import LoanEligibilityResult, LoanRequest

@pytest.fixture
def bank_user(db):
    u = User.objects.create_user(email="bank@x.com", password="Pass123!", role=User.Role.BANK_USER)
    c = APIClient()
    r = c.post("/api/auth/login/", {"email": "bank@x.com", "password": "Pass123!"}, format="json")
    assert r.status_code == 200
    token = r.data["access"]
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return u, c

@pytest.fixture
def spo_with_data(db):
    spo = User.objects.create_user(email="spo@x.com", password="Pass123!", role=User.Role.SPO, first_name="A", last_name="B")
    org = Organization.objects.create(name="GreenTech", registration_type=Organization.RegistrationType.PRIVATE_LTD, created_by=spo, focus_area="Health", poc_email="poc@x.com")
    # one submitted assessment with scores
    a1 = Assessment.objects.create(
        organization=org, status="SUBMITTED",
        started_at=timezone.now() - timezone.timedelta(days=2),
        submitted_at=timezone.now() - timezone.timedelta(days=1),
        scores={"overall": 72, "sections": {"IMPACT": 90, "RISK": 20, "RETURN": 90}},
    )
    LoanEligibilityResult.objects.create(
        assessment=a1, overall_score=72, is_eligible=True, details={"sections": {}}, evaluated_at=timezone.now()
    )
    lr = LoanRequest.objects.create(
        assessment=a1, organization=org, applicant=spo,
        founder_name="F", founder_email="f@x.com", amount_in_inr="500000.00",
        fund_type="GROWTH_CAPITAL", status="SUBMITTED", submitted_at=timezone.now()
    )
    return spo, org, a1, lr

@pytest.mark.django_db
def test_bank_spo_list(bank_user, spo_with_data):
    _, c = bank_user
    r = c.get("/api/bank/spos/?limit=50")
    assert r.status_code == 200
    body = r.json()
    assert "results" in body and len(body["results"]) >= 1
    row = next(x for x in body["results"] if x["email"] == "spo@x.com")
    assert row["id"] > 0
    assert row["focus_sector"] == "Health"
    assert row["last_assessment_submitted_at"] is not None
    assert row["last_loan_request_submitted_at"] is not None

@pytest.mark.django_db
def test_bank_spo_detail(bank_user, spo_with_data):
    (spo, org, a1, lr) = spo_with_data
    _, c = bank_user
    r = c.get(f"/api/bank/spos/{spo.id}/")
    assert r.status_code == 200
    body = r.json()
    assert body["spo"]["email"] == "spo@x.com"
    assert body["organization"]["name"] == "GreenTech"
    assert body["assessments"][0]["id"] == a1.id
    assert body["assessments"][0]["eligibility_decision"] is True
    assert body["assessments"][0]["loan_request_id"] == lr.id

@pytest.mark.django_db
def test_bank_spo_report_pdf(bank_user, spo_with_data):
    (spo, *_rest) = spo_with_data
    _, c = bank_user
    r = c.get(f"/api/bank/spos/{spo.id}/report/")
    assert r.status_code == 200
    assert r["Content-Type"] == "application/pdf"
    assert r["Content-Disposition"].startswith(f'attachment; filename="bank-spo-{spo.id}.pdf"')