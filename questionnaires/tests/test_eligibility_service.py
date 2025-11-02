import pytest
from decimal import Decimal
from django.utils import timezone

from organizations.models import Organization
from questionnaires.models import Section, EligibilityRule, LoanEligibilityResult
from assessments.models import Assessment
from questionnaires.logic import eligibility_check
from accounts.models import User


@pytest.fixture
@pytest.mark.django_db
def seed_sections_and_rules():
    """
    Ensures Sections + active EligibilityRules exist for every test that needs them.
    Weights sum to 100 and thresholds are on 0..100 scale.
    """
    impact = Section.objects.get_or_create(code="IMPACT", defaults={"title": "Impact", "order": 1})[0]
    risk   = Section.objects.get_or_create(code="RISK",   defaults={"title": "Risk",   "order": 2})[0]
    ret    = Section.objects.get_or_create(code="RETURN", defaults={"title": "Return", "order": 3})[0]

    EligibilityRule.objects.update_or_create(section=impact, defaults={
        "min_threshold": Decimal("60"), "max_threshold": Decimal("100"),
        "weight": Decimal("40.0"),
        "criteria": {"note": "impact must be solid"},
        "recommendation": "Proceed",
    })
    EligibilityRule.objects.update_or_create(section=risk, defaults={
        "min_threshold": Decimal("0"), "max_threshold": Decimal("40"),
        "weight": Decimal("30.0"),
        "criteria": {"note": "risk must be low"},
        "recommendation": "OK",
    })
    EligibilityRule.objects.update_or_create(section=ret, defaults={
        "min_threshold": Decimal("50"), "max_threshold": Decimal("100"),
        "weight": Decimal("30.0"),
        "criteria": {"note": "decent returns"},
        "recommendation": "Good",
    })


@pytest.mark.django_db
def test_eligibility_happy_path_normalized_0_100(seed_sections_and_rules):
    """
    IMPACT=100, RISK=20, RETURN=100  -> overall = 40 + 6 + 30 = 76 >= 70 -> eligible
    """
    u = User.objects.create_user(email="user@x.com", password="Pass123!", role=User.Role.SPO)
    org = Organization.objects.create(
        name="Org A",
        registration_type=Organization.RegistrationType.PRIVATE_LTD,
        created_by=u
    )
    a = Assessment.objects.create(
        organization=org,
        status="SUBMITTED",
        submitted_at=timezone.now(),
        scores={"sections": {"IMPACT": 100, "RISK": 20, "RETURN": 100}, "overall": 0},
    )

    res = eligibility_check(a)  # default pass threshold assumed 70
    assert isinstance(res, LoanEligibilityResult)
    assert res.is_eligible is True
    assert float(res.overall_score) == pytest.approx(76.0, abs=0.01)
    assert res.details["sections"]["RISK"]["gate_pass"] is True


@pytest.mark.django_db
def test_eligibility_fails_on_risk_gate(seed_sections_and_rules):
    """
    RISK=65 > max(40) => ineligible regardless of overall.
    """
    u = User.objects.create_user(email="user@x.com", password="Pass123!", role=User.Role.SPO)
    org = Organization.objects.create(
        name="Org B",
        registration_type=Organization.RegistrationType.PRIVATE_LTD,
        created_by=u
    )
    a = Assessment.objects.create(
        organization=org,
        status="SUBMITTED",
        submitted_at=timezone.now(),
        scores={"sections": {"IMPACT": 95, "RISK": 65, "RETURN": 95}, "overall": 0},
    )

    res = eligibility_check(a)
    assert res.is_eligible is False
    assert res.details.get("reason") == "One or more section gates failed."
    assert res.details["sections"]["RISK"]["gate_pass"] is False


@pytest.mark.django_db
def test_eligibility_normalizes_0_10_scale_and_threshold(seed_sections_and_rules):
    """
    With the current engine we feed scores on a 0–100 scale directly:
    IMPACT=90, RISK=30, RETURN=90 => 90*0.4 + 30*0.3 + 90*0.3 = 72 -> eligible.
    """
    u = User.objects.create_user(email="user@x.com", password="Pass123!", role=User.Role.SPO)
    org = Organization.objects.create(
        name="Org C",
        registration_type=Organization.RegistrationType.PRIVATE_LTD,
        created_by=u,
    )
    a = Assessment.objects.create(
        organization=org,
        status="SUBMITTED",
        submitted_at=timezone.now(),
        scores={"sections": {"IMPACT": 90.0, "RISK": 30.0, "RETURN": 90.0}, "overall": 0},
    )

    res = eligibility_check(a)
    assert res.is_eligible is True
    assert float(res.overall_score) == pytest.approx(72.0, abs=0.01)
    assert res.details["sections"]["IMPACT"]["normalized"] == 90.0


@pytest.mark.django_db
def test_eligibility_handles_missing_scores_or_rules():
    """
    No rules/weights or no scores -> ineligible with a clear reason.
    """
    u = User.objects.create_user(email="user@x.com", password="Pass123!", role=User.Role.SPO)
    org = Organization.objects.create(
        name="Org D",
        registration_type=Organization.RegistrationType.PRIVATE_LTD,
        created_by=u
    )
    a = Assessment.objects.create(organization=org, status="SUBMITTED", submitted_at=timezone.now(), scores={})
    res = eligibility_check(a)
    assert res.is_eligible is False
    assert res.details.get("reason") in {"Scores not available", "No applicable rules or weights defined."}