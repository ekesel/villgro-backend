import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

@pytest.mark.django_db
def test_feedback_meta_requires_auth():
    c = APIClient()
    r = c.get("/api/feedback/meta")
    assert r.status_code in (401, 403)  # depends on your auth middleware

@pytest.mark.django_db
def test_feedback_meta_happy_path():
    User = get_user_model()
    u = User.objects.create_user(email="spo@x.com", password="Pass123!", role=User.Role.SPO)

    c = APIClient()
    c.force_authenticate(user=u)

    r = c.get("/api/feedback/meta")
    assert r.status_code == 200

    data = r.json()
    assert "reasons" in data and isinstance(data["reasons"], list)
    keys = {item["key"] for item in data["reasons"]}
    labels_present = all("label" in item for item in data["reasons"])

    # The choices are sourced from AssessmentFeedback.Reason
    expected = {
        "hard_to_understand",
        "too_long",
        "irrelevant",
        "come_back_later",
        "other",
    }
    assert expected.issubset(keys)
    assert labels_present