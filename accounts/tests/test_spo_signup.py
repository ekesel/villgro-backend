import pytest
from rest_framework.test import APIClient
from django.contrib.auth import get_user_model
from organizations.models import Organization

User = get_user_model()

@pytest.mark.django_db
def test_spo_two_step_signup_flow():
    client = APIClient()

    # Step 1: start signup -> creates user + returns tokens
    start_payload = {
        "email": "founder@example.com",
        "password": "StrongPass123!",
        "confirm_password": "StrongPass123!",
        "first_name": "Asha",
        "last_name": "Verma",
        "phone": "9999999999",
        "agree_to_terms": True
    }
    resp1 = client.post("/api/auth/spo-signup/start/", start_payload, format="json")
    assert resp1.status_code == 201
    assert "tokens" in resp1.data and "access" in resp1.data["tokens"]
    assert User.objects.filter(email="founder@example.com").exists()

    access = resp1.data["tokens"]["access"]

    # Step 2: complete profile -> creates organization
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    complete_payload = {
        "org_name": "Eco Innovations",
        "registration_type": "PRIVATE_LTD",
        "gst_number": "27ABCDE1234F1Z5",
        "cin_number": "U00000MH2021PTC000000"
    }
    resp2 = client.post("/api/auth/spo-signup/complete/", complete_payload, format="json")
    assert resp2.status_code == 201
    user = User.objects.get(email="founder@example.com")
    user.refresh_from_db()
    assert user.terms_accepted is True
    assert user.terms_accepted_at is not None
    assert hasattr(user, "organization")
    assert Organization.objects.filter(created_by=user, name="Eco Innovations").exists()
    resp3 = client.post(
        "/api/auth/spo-signup/complete/",
        {"gst_number": "27ABCDE9999F1Z9"},
        format="json",
    )
    assert resp3.status_code == 200
    assert Organization.objects.get(created_by=user).gst_number == "27ABCDE9999F1Z9"