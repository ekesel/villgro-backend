import pytest
from rest_framework.test import APIClient
from django.contrib.auth import get_user_model

User = get_user_model()

@pytest.mark.django_db
def test_save_and_resume_progress_with_flags():
    u = User.objects.create_user(email="founder@example.com", password="StrongPass123!", role=User.Role.SPO)
    client = APIClient()
    login = client.post("/api/auth/login/", {"email":"founder@example.com","password":"StrongPass123!"}, format="json")
    access = login.data["access"]
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    # GET should create progress row and include has_completed flag when added in payload
    r1 = client.get("/api/onboarding")
    assert r1.status_code == 200
    assert r1.data["current_step"] == 1
    assert r1.data["is_complete"] is False
    # some implementations add this for convenience:
    # assert r1.data.get("has_completed_profile") is False

    # Save partial for step 2
    payload = {"current_step": 2, "data": {"step2": {"type_of_innovation":"Product","geo_scope":"Across states"}}}
    r2 = client.patch("/api/onboarding", payload, format="json")
    assert r2.status_code == 200
    assert r2.data["current_step"] == 2
    assert r2.data["data"]["step2"]["type_of_innovation"] == "Product"

    # Resume (GET) shows same
    r3 = client.get("/api/onboarding")
    assert r3.status_code == 200
    assert r3.data["current_step"] == 2
    assert r3.data["data"]["step2"]["geo_scope"] == "Across states"

    # Advance to step 3
    r4 = client.post("/api/onboarding/advance", {"to_step":3}, format="json")
    assert r4.status_code == 200
    assert r4.data["current_step"] == 3