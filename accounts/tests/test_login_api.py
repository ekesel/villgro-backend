import pytest
from rest_framework.test import APIClient
from organizations.models import OnboardingProgress
from accounts.models import User

@pytest.mark.django_db
def test_login_returns_tokens_user_and_onboarding_flags():
    user = User.objects.create_user(
        email="founder@example.com",
        password="StrongPass123!",
        first_name="Asha",
        last_name="Verma",
        role=User.Role.SPO
    )
    client = APIClient()

    # Act: login
    resp = client.post("/api/auth/login/", {
        "email": "founder@example.com",
        "password": "StrongPass123!"
    }, format="json")

    # Assert tokens + user payload
    assert resp.status_code == 200
    body = resp.json()
    assert "access" in body and "refresh" in body
    assert body["user"]["email"] == "founder@example.com"

    # On first login, progress is auto-created and not complete
    assert body["has_completed_profile"] is False
    assert "onboarding" in body
    assert body["onboarding"]["current_step"] == 1
    assert body["onboarding"]["is_complete"] is False

    # DB check
    prog = OnboardingProgress.objects.get(user=user)
    assert prog.current_step == 1
    assert prog.is_complete is False