import pytest
from rest_framework.test import APIClient
from django.utils import timezone

from accounts.models import User
from organizations.models import Organization
from assessments.models import Assessment, AssessmentFeedback


@pytest.mark.django_db
class TestAdminReviews:
    def _admin(self, email="admin@x.com"):
        return User.objects.create_user(
            email=email, password="Pass123!", role=User.Role.ADMIN, is_staff=True
        )

    def _spo_with_assessment_and_feedback(self, email, org_name, status, reasons=None, comment=""):
        spo = User.objects.create_user(email=email, password="Pass123!", role=User.Role.SPO)
        org = Organization.objects.create(
            name=org_name,
            registration_type=Organization.RegistrationType.PRIVATE_LTD,
            created_by=spo,
        )
        a = Assessment.objects.create(
            organization=org,
            status=status,
            started_at=timezone.now(),
            submitted_at=timezone.now() if status == "SUBMITTED" else None,
        )
        AssessmentFeedback.objects.create(assessment=a, reasons=reasons or [], comment=comment)
        return spo, org, a

    def test_reviews_list_filters_and_detail(self):
        admin = self._admin()

        # One completed, one incomplete
        _, _, a1 = self._spo_with_assessment_and_feedback(
            "spo1@x.com", "GreenTech Pvt", "SUBMITTED", reasons=["too_long"], comment="ok"
        )
        _, _, a2 = self._spo_with_assessment_and_feedback(
            "spo2@x.com", "HealthWorks", "DRAFT", reasons=["irrelevant"], comment="later"
        )

        c = APIClient()
        c.force_authenticate(user=admin)

        # list all
        r_all = c.get("/api/admin/reviews/")
        assert r_all.status_code == 200
        assert r_all.data["count"] == 2
        row_keys = {"id", "assessment_id", "user_id", "user_email", "organization_name", "status", "review"}
        assert row_keys.issubset(set(r_all.data["results"][0].keys()))

        # filter completed
        r_completed = c.get("/api/admin/reviews/?status=completed")
        assert r_completed.status_code == 200
        assert r_completed.data["count"] == 1
        assert r_completed.data["results"][0]["assessment_id"] == a1.id
        assert r_completed.data["results"][0]["status"].lower() == "completed"

        # filter incomplete
        r_incomplete = c.get("/api/admin/reviews/?status=incomplete")
        assert r_incomplete.status_code == 200
        assert r_incomplete.data["count"] == 1
        assert r_incomplete.data["results"][0]["assessment_id"] == a2.id
        assert r_incomplete.data["results"][0]["status"].lower() == "incomplete"

        # search by org/user/comment
        r_search = c.get("/api/admin/reviews/?q=green")
        assert r_search.status_code == 200
        assert r_search.data["count"] == 1
        assert r_search.data["results"][0]["organization_name"] == "GreenTech Pvt"

        # ordering by id asc
        r_ord = c.get("/api/admin/reviews/?ordering=id")
        assert r_ord.status_code == 200
        ids = [row["id"] for row in r_ord.data["results"]]
        assert ids == sorted(ids)

        # retrieve detail
        fb_id = r_all.data["results"][0]["id"]
        r_det = c.get(f"/api/admin/reviews/{fb_id}/")
        assert r_det.status_code == 200
        assert {"id", "assessment_id", "user", "organization", "status", "reasons", "comment"}.issubset(set(r_det.data.keys()))

    def test_reviews_requires_admin(self):
        # SPO cannot access
        spo = User.objects.create_user(email="spo@x.com", password="x", role=User.Role.SPO)
        c = APIClient()
        c.force_authenticate(user=spo)
        r = c.get("/api/admin/reviews/")
        assert r.status_code in (401, 403)