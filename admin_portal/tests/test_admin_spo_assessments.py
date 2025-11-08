import pytest
from rest_framework.test import APIClient
from django.utils import timezone

from django.contrib.auth import get_user_model
from organizations.models import Organization
from assessments.models import Assessment
from questionnaires.models import LoanEligibilityResult, LoanInstrument

User = get_user_model()


@pytest.mark.django_db
def test_admin_spo_assessments_happy_path():
    admin = User.objects.create_user(email="admin@x.com", password="Pass123!", role=User.Role.ADMIN, is_staff=True)

    spo = User.objects.create_user(email="spo@x.com", password="Pass123!", role=User.Role.SPO)
    org = Organization.objects.create(
        name="Acme Climate",
        registration_type=Organization.RegistrationType.PRIVATE_LTD,
        created_by=spo,
    )

    a1 = Assessment.objects.create(
        organization=org,
        status="SUBMITTED",
        started_at=timezone.now(),
        submitted_at=timezone.now(),
        scores={"overall": 76.5, "sections": {"IMPACT": 79, "RISK": 72, "RETURN": 78}},
    )
    a2 = Assessment.objects.create(
        organization=org,
        status="DRAFT",
        started_at=timezone.now(),
        scores={"overall": 50, "sections": {"IMPACT": 55, "RISK": 45, "RETURN": 50}},
    )

    # Optional: add an instrument and eligibility for a1
    inst = LoanInstrument.objects.create(name="Commercial Debt with Impact")
    LoanEligibilityResult.objects.create(
        assessment=a1,
        overall_score=72.0,
        is_eligible=True,
        matched_instrument=inst,
        details={"reason": None},
    )

    c = APIClient()
    c.force_authenticate(user=admin)

    r = c.get(f"/api/admin/spos/{spo.id}/assessments/")
    assert r.status_code == 200
    assert isinstance(r.data, list)

    # We expect both assessments
    ids = [row["id"] for row in r.data]
    assert a1.id in ids and a2.id in ids

    row = next(x for x in r.data if x["id"] == a1.id)
    assert row["scores"]["overall"] == 76.5
    assert row["scores"]["sections"]["IMPACT"] == 79
    assert row["eligibility"]["is_eligible"] is True
    assert row["eligibility"]["overall_score"] == 72.0
    assert row["instrument"]["name"] == "Commercial Debt with Impact"

    # The DRAFT one likely has no eligibility/instrument yet
    row2 = next(x for x in r.data if x["id"] == a2.id)
    assert row2["eligibility"] is None
    assert row2["instrument"] is None


@pytest.mark.django_db
def test_admin_spo_assessments_requires_admin():
    spo = User.objects.create_user(email="spo@x.com", password="Pass123!", role=User.Role.SPO)
    org = Organization.objects.create(
        name="Acme Climate",
        registration_type=Organization.RegistrationType.PRIVATE_LTD,
        created_by=spo,
    )
    Assessment.objects.create(organization=org, status="DRAFT")

    c = APIClient()
    c.force_authenticate(user=spo)

    r = c.get(f"/api/admin/spos/{spo.id}/assessments/")
    assert r.status_code in (401, 403)


@pytest.mark.django_db
def test_admin_spo_assessments_404_without_org():
    admin = User.objects.create_user(email="admin@x.com", password="Pass123!", role=User.Role.ADMIN, is_staff=True)
    spo_no_org = User.objects.create_user(email="noorg@x.com", password="Pass123!", role=User.Role.SPO)

    c = APIClient()
    c.force_authenticate(user=admin)

    r = c.get(f"/api/admin/spos/{spo_no_org.id}/assessments/")
    assert r.status_code == 404