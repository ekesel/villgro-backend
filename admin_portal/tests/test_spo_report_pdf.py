import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from django.contrib.auth import get_user_model
from organizations.models import Organization
from assessments.models import Assessment
from questionnaires.models import Section, EligibilityRule
from questionnaires.logic import eligibility_check

User = get_user_model()


def login(client: APIClient, email: str, password: str) -> str:
    resp = client.post("/api/auth/login/", {"email": email, "password": password}, format="json")
    assert resp.status_code == 200, resp.content
    return resp.data["access"]


def admin_client(email="admin@example.com", password="Pass123!"):
    admin = User.objects.create_user(email=email, password=password, role=getattr(User.Role, "ADMIN", "ADMIN"))
    c = APIClient()
    token = login(c, email, password)
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return admin, c


def spo_with_org(email="spo@example.com", password="Pass123!"):
    spo = User.objects.create_user(email=email, password=password, role=getattr(User.Role, "SPO", "SPO"))
    # Link an Organization the way the app expects (reverse name is `organization`)
    org = Organization.objects.create(name="Acme Climate", registration_type=Organization.RegistrationType.PRIVATE_LTD, created_by=spo)
    # If your model provides a 1-1 backref `organization` on User, the above is enough.
    return spo, org


@pytest.fixture
def seed_rules(db):
    """
    Minimal eligibility rules so eligibility_check() can compute & persist results.
    """
    impact = Section.objects.get_or_create(code="IMPACT", defaults={"title": "Impact", "order": 1})[0]
    risk   = Section.objects.get_or_create(code="RISK",   defaults={"title": "Risk",   "order": 2})[0]
    ret    = Section.objects.get_or_create(code="RETURN", defaults={"title": "Return", "order": 3})[0]

    EligibilityRule.objects.update_or_create(section=impact, defaults={"min_threshold": 60, "max_threshold": 100, "weight": 40})
    EligibilityRule.objects.update_or_create(section=risk,   defaults={"min_threshold":  0, "max_threshold":  40, "weight": 30})
    EligibilityRule.objects.update_or_create(section=ret,    defaults={"min_threshold": 55, "max_threshold": 100, "weight": 30})


@pytest.mark.django_db
def test_report_pdf_happy_path(seed_rules):
    # Admin auth
    admin, client = admin_client()

    # SPO + Org + a couple of assessments (submitted)
    spo, org = spo_with_org()

    a1 = Assessment.objects.create(
        organization=org,
        status="SUBMITTED",
        started_at=timezone.now() - timezone.timedelta(days=2),
        submitted_at=timezone.now() - timezone.timedelta(days=1),
        scores={"sections": {"IMPACT": 90, "RISK": 20, "RETURN": 85}, "overall": 0},
    )
    a2 = Assessment.objects.create(
        organization=org,
        status="SUBMITTED",
        started_at=timezone.now() - timezone.timedelta(days=1, hours=6),
        submitted_at=timezone.now(),
        scores={"sections": {"IMPACT": 70, "RISK": 15, "RETURN": 90}, "overall": 0},
    )

    # Persist eligibility results for table display
    eligibility_check(a1)
    eligibility_check(a2)

    # Hit: GET /api/admin/spos/{id}/report/
    url = f"/api/admin/spos/{spo.id}/report/"
    resp = client.get(url)

    assert resp.status_code == 200, resp.content
    # Content-Type should be a PDF
    assert resp["Content-Type"] == "application/pdf"
    # Should send a filename
    assert "Content-Disposition" in resp
    assert f'spo-report-{spo.id}.pdf' in resp["Content-Disposition"]
    # WeasyPrint PDFs start with %PDF-
    assert resp.content[:5] == b"%PDF-"


@pytest.mark.django_db
def test_report_pdf_requires_admin(seed_rules):
    # SPO logs in (not Admin)
    spo_user = User.objects.create_user(email="spo@x.com", password="Pass123!", role=getattr(User.Role, "SPO", "SPO"))
    _org = Organization.objects.create(name="Solo Org", registration_type=Organization.RegistrationType.PRIVATE_LTD, created_by=spo_user)

    c = APIClient()
    token = login(c, "spo@x.com", "Pass123!")
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    url = f"/api/admin/spos/{spo_user.id}/report/"
    resp = c.get(url)
    # Should be forbidden by IsAdminRole
    assert resp.status_code in (401, 403)


@pytest.mark.django_db
def test_report_pdf_404_when_no_org(seed_rules):
    # Admin auth
    admin, client = admin_client()

    # SPO WITHOUT an organization linked
    spo = User.objects.create_user(email="orphanspo@example.com", password="Pass123!", role=getattr(User.Role, "SPO", "SPO"))

    url = f"/api/admin/spos/{spo.id}/report/"
    resp = client.get(url)

    assert resp.status_code == 404
    body = resp.json()
    assert body.get("detail") in ("Organization not found for this SPO", "Not found")