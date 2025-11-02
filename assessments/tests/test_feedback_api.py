import pytest
from rest_framework.test import APIClient
from django.utils import timezone

from accounts.models import User
from organizations.models import Organization
from assessments.models import Assessment, AssessmentFeedback


@pytest.mark.django_db
class TestSPOFeedbackAPI:
    def _spo(self, email="spo@x.com"):
        u = User.objects.create_user(email=email, password="Pass123!", role=User.Role.SPO)
        org = Organization.objects.create(
            name="Acme Pvt",
            registration_type=Organization.RegistrationType.PRIVATE_LTD,
            created_by=u,
        )
        return u, org

    def test_feedback_submit_then_get(self):
        u, org = self._spo()
        a = Assessment.objects.create(organization=org, status="DRAFT")

        c = APIClient()
        c.force_authenticate(user=u)

        # submit new feedback (should create -> 201)
        payload = {
            "assessment": a.id,
            "reasons": ["too_long", "come_back_later"],
            "comment": "Will finish later",
        }
        r = c.post("/api/feedback", payload, format="json")
        assert r.status_code in (200, 201)
        fb = AssessmentFeedback.objects.get(assessment=a)
        assert set(fb.reasons) == {"too_long", "come_back_later"}
        assert fb.comment == "Will finish later"

        # update same assessment feedback (upsert -> 200)
        r2 = c.post(
            "/api/feedback",
            {"assessment": a.id, "reasons": ["irrelevant"], "comment": ""},
            format="json",
        )
        assert r2.status_code in (200, 201)
        fb.refresh_from_db()
        assert fb.reasons == ["irrelevant"]
        assert fb.comment == ""

        # fetch via GET
        r3 = c.get(f"/api/feedback?assessment_id={a.id}")
        assert r3.status_code == 200
        j = r3.json()
        assert j["assessment"] == a.id
        assert j["reasons"] == ["irrelevant"]

    def test_feedback_forbidden_for_non_owner(self):
        owner, org = self._spo("owner@x.com")
        other, _ = self._spo("other@x.com")  # has its own org
        a = Assessment.objects.create(organization=org, status="DRAFT")

        c = APIClient()
        c.force_authenticate(user=other)

        r = c.post(
            "/api/feedback",
            {"assessment": a.id, "reasons": ["too_long"], "comment": "x"},
            format="json",
        )
        # validation should reject because 'other' doesn't own this assessment's org
        assert r.status_code == 400
        assert "not allowed" in str(r.data).lower()