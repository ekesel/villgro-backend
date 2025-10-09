import pytest
from rest_framework.test import APIClient
from django.contrib.auth import get_user_model
from organizations.models import OnboardingProgress, Organization

User = get_user_model()

@pytest.mark.django_db
def test_signup_complete_creates_org_and_sets_step2():
    # user signs up (step-1 start already done elsewhere), here we simulate an authenticated SPO
    user = User.objects.create_user(email="founder@example.com", password="StrongPass123!", role=User.Role.SPO)
    client = APIClient()
    login = client.post("/api/auth/login/", {"email":"founder@example.com","password":"StrongPass123!"}, format="json")
    access = login.data["access"]
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    # Call the 'complete' endpoint (legal/org basics)
    payload = {
        "org_name": "Eco Innovations",
        "registration_type": "PRIVATE_LTD",
        # optional fields may be omitted in this test
    }
    resp = client.post("/api/auth/spo-signup/complete/", payload, format="json")
    assert resp.status_code == 201

    # Organization created and linked
    assert Organization.objects.filter(created_by=user, name="Eco Innovations").exists()

    # Progress advanced to step 2, still not complete
    prog = OnboardingProgress.objects.get(user=user)
    assert prog.current_step == 2
    assert prog.is_complete is False

    # Response flags for FE routing
    assert resp.data["has_completed_profile"] is False
    assert resp.data["onboarding"]["current_step"] == 2
    assert resp.data["onboarding"]["is_complete"] is False
    resp_update = client.post(
        "/api/auth/spo-signup/complete/",
        {"cin_number": "U12345MH2021PTC111111"},
        format="json",
    )
    assert resp_update.status_code == 200

    org = Organization.objects.get(created_by=user)
    assert org.cin_number == "U12345MH2021PTC111111"

    prog.refresh_from_db()
    assert prog.current_step == 2 and prog.is_complete is False