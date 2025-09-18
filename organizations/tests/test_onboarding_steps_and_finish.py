import pytest
from rest_framework.test import APIClient
from django.contrib.auth import get_user_model
from organizations.models import Organization, OnboardingProgress

User = get_user_model()

@pytest.mark.django_db
def test_step2_step3_and_finish_flow():
    # Setup user + org (simulate Step 1 done)
    user = User.objects.create_user(email="f@example.com", password="StrongPass123!", role=User.Role.SPO)
    org = Organization.objects.create(
        name="Eco Innovations",
        registration_type=Organization.RegistrationType.PRIVATE_LTD,
        created_by=user
    )

    client = APIClient()
    login = client.post("/api/auth/login/", {"email":"f@example.com","password":"StrongPass123!"}, format="json")
    access = login.data["access"]
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    # Step 2 save
    r2 = client.patch("/api/onboarding/step/2", {
        "type_of_innovation": "PRODUCT",
        "geo_scope": "STATES",
        "top_states": ["Karnataka","Maharashtra"]
    }, format="json")
    assert r2.status_code == 200
    org.refresh_from_db()
    assert org.type_of_innovation == "PRODUCT"
    assert org.geo_scope == "STATES"
    assert org.top_states == ["Karnataka","Maharashtra"]

    prog = OnboardingProgress.objects.get(user=user)
    assert prog.current_step >= 3

    # Step 3 save
    r3 = client.patch("/api/onboarding/step/3", {
        "focus_sector": "AGRICULTURE",
        "org_stage": "EARLY_REVENUE",
        "impact_focus": "BOTH",
        "annual_operating_budget": "2500000.00",
        "use_of_questionnaire": "FUNDING",
        "received_philanthropy_before": True
    }, format="json")
    assert r3.status_code == 200

    org.refresh_from_db()
    assert org.focus_sector == "AGRICULTURE"
    assert str(org.annual_operating_budget) == "2500000.00"

    # Finish
    r4 = client.post("/api/onboarding/finish", {}, format="json")
    assert r4.status_code == 200
    prog.refresh_from_db()
    assert prog.is_complete is True
    assert prog.current_step >= 3