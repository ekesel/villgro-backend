import pytest
from django.urls import reverse
from django.utils import timezone
from assessments.models import Assessment

pytestmark = pytest.mark.django_db

def test_pdf_report_download(api_client):
    client, user = api_client

    # Create a submitted assessment
    assessment = Assessment.objects.create(
        organization=user.organization,
        status="SUBMITTED",
        submitted_at=timezone.now(),
        scores={"sections": {"IMPACT": 7.5, "RISK": 6.0}, "overall": 6.75},
    )

    url = reverse("assessment-report-pdf", args=[assessment.id])
    resp = client.get(url)

    # Assertions
    assert resp.status_code == 200
    assert resp["Content-Type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF-")
    assert f"assessment-{assessment.id}.pdf" in resp["Content-Disposition"]


def test_pdf_report_unsubmitted_not_allowed(api_client):
    client, user = api_client

    # Create only a draft
    draft = Assessment.objects.create(
        organization=user.organization,
        status="DRAFT",
        scores={},
    )

    url = reverse("assessment-report-pdf", args=[draft.id])
    resp = client.get(url)

    # Should not allow PDF generation for drafts
    assert resp.status_code == 404