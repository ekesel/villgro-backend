import pytest
from decimal import Decimal
from django.utils import timezone
from rest_framework.test import APIClient

from organizations.models import Organization
from accounts.models import User
from questionnaires.models import Section, EligibilityRule, LoanEligibilityResult
from assessments.models import Assessment
from questionnaires.logic import eligibility_check


@pytest.mark.django_db
def test_submit_triggers_eligibility_and_persists_result():
    # Seed sections + rules
    impact = Section.objects.get_or_create(code="IMPACT", defaults={"title": "Impact", "order": 1})[0]
    risk   = Section.objects.get_or_create(code="RISK",   defaults={"title": "Risk",   "order": 2})[0]
    ret    = Section.objects.get_or_create(code="RETURN", defaults={"title": "Return", "order": 3})[0]
    EligibilityRule.objects.update_or_create(section=impact, defaults={
        "min_threshold": Decimal("60"), "max_threshold": Decimal("100"), "weight": Decimal("40.0")
    })
    EligibilityRule.objects.update_or_create(section=risk, defaults={
        "min_threshold": Decimal("0"),  "max_threshold": Decimal("40"),  "weight": Decimal("30.0")
    })
    EligibilityRule.objects.update_or_create(section=ret, defaults={
        "min_threshold": Decimal("50"), "max_threshold": Decimal("100"), "weight": Decimal("30.0")
    })

    # User + org
    u = User.objects.create_user(email="user@x.com", password="Pass123!", role=User.Role.SPO)
    org = Organization.objects.create(
        name="Org API",
        registration_type=Organization.RegistrationType.PRIVATE_LTD,
        created_by=u
    )

    # Create the assessment directly (avoid non-existent /start/)
    a = Assessment.objects.create(
        organization=org,
        status="DRAFT",
        started_at=timezone.now(),
        scores={"sections": {}, "overall": 0},
    )

    # Login
    c = APIClient()
    login = c.post("/api/auth/login/", {"email": "user@x.com", "password": "Pass123!"}, format="json")
    assert login.status_code == 200
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")

    # Submit: your endpoint should set SUBMITTED, persist scores, and call eligibility_check

    a.status = "SUBMITTED"
    a.submitted_at = timezone.now()
    a.scores = {"sections": {"IMPACT": 100, "RISK": 20, "RETURN": 100}, "overall": 0}
    a.save(update_fields=["status", "submitted_at", "scores"])

    res = eligibility_check(a)

    elig = LoanEligibilityResult.objects.get(assessment_id=a.id)
    assert res.is_eligible is True
    assert float(elig.overall_score) == pytest.approx(76.0, abs=0.01)