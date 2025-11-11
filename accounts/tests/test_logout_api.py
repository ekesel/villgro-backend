import pytest
from rest_framework.test import APIClient
from accounts.models import User

@pytest.mark.django_db
def test_logout_blacklists_refresh_token():
    user = User.objects.create_user(email="founder@example.com", password="StrongPass123!")
    client = APIClient()
    # Login to get tokens
    login = client.post("/api/auth/login/", {"email":"founder@example.com","password":"StrongPass123!"}, format="json")
    refresh = login.data["refresh"]

    # Logout using refresh
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
    resp = client.post("/api/auth/logout/", {"refresh": refresh}, format="json")
    assert resp.status_code == 205
    assert resp.data["message"] == "Logout successful. Token blacklisted."

    # Try to use blacklisted refresh token again
    resp2 = client.post("/api/auth/refresh/", {"refresh": refresh}, format="json")
    assert resp2.status_code == 401  # invalid since blacklisted