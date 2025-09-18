import pytest
from rest_framework.test import APIClient
from django.contrib.auth import get_user_model

User = get_user_model()

@pytest.mark.django_db
def test_change_password_success_and_requires_relogin():
    u = User.objects.create_user(email="user@x.com", password="OldPass123!", role=User.Role.SPO)
    client = APIClient()
    login = client.post("/api/auth/login/", {"email":"user@x.com","password":"OldPass123!"}, format="json")
    access = login.data["access"]
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    resp = client.post("/api/auth/change-password/", {
        "current_password": "OldPass123!",
        "new_password": "NewPass123!",
        "confirm_password": "NewPass123!"
    }, format="json")
    assert resp.status_code == 205

    # Old password should fail
    bad = client.post("/api/auth/login/", {"email":"user@x.com","password":"OldPass123!"}, format="json")
    assert bad.status_code == 401

    # New password works
    ok = client.post("/api/auth/login/", {"email":"user@x.com","password":"NewPass123!"}, format="json")
    assert ok.status_code == 200

@pytest.mark.django_db
def test_change_password_wrong_current():
    u = User.objects.create_user(email="user@x.com", password="OldPass123!", role=User.Role.SPO)
    client = APIClient()
    login = client.post("/api/auth/login/", {"email":"user@x.com","password":"OldPass123!"}, format="json")
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")

    resp = client.post("/api/auth/change-password/", {
        "current_password": "WRONG!",
        "new_password": "NewPass123!",
        "confirm_password": "NewPass123!"
    }, format="json")
    assert resp.status_code == 400
    assert "current_password" in resp.data