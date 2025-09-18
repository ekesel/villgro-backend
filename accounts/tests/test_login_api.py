import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

User = get_user_model()

@pytest.mark.django_db
def test_login_returns_tokens_and_user_info():
    # Arrange: create a user
    u = User.objects.create_user(
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

    # Assert
    assert resp.status_code == 200
    body = resp.json()
    assert "access" in body and "refresh" in body
    assert body["user"]["email"] == "founder@example.com"
    assert body["user"]["role"] == "SPO"
    assert body["user"]["has_completed_profile"] is False  # no org yet

@pytest.mark.django_db
def test_refresh_returns_new_access_token():
    u = User.objects.create_user(email="x@y.com", password="StrongPass123!")
    client = APIClient()
    # first login to get refresh
    login = client.post("/api/auth/login/", {"email":"x@y.com","password":"StrongPass123!"}, format="json")
    refresh = login.data["refresh"]

    resp = client.post("/api/auth/refresh/", {"refresh": refresh}, format="json")
    assert resp.status_code == 200
    assert "access" in resp.data