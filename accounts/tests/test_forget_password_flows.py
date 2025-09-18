import pytest
from rest_framework.test import APIClient
from django.contrib.auth import get_user_model
from accounts.models import PasswordResetCode

User = get_user_model()

@pytest.mark.django_db
def test_forgot_password_flow():
    user = User.objects.create_user(email="founder@example.com", password="OldPass123!")
    client = APIClient()

    # Step 1: request reset
    resp1 = client.post("/api/auth/password/forgot/", {"email": "founder@example.com"}, format="json")
    assert resp1.status_code == 200
    code_obj = PasswordResetCode.objects.get(user=user)
    code = code_obj.code

    # Step 2: verify code
    resp2 = client.post("/api/auth/password/verify-code/", {"email": "founder@example.com", "code": code}, format="json")
    assert resp2.status_code == 200

    # Step 3: reset password
    resp3 = client.post("/api/auth/password/reset/", {
        "email": "founder@example.com",
        "code": code,
        "new_password": "NewPass123!",
        "confirm_password": "NewPass123!"
    }, format="json")
    assert resp3.status_code == 200

    user.refresh_from_db()
    assert user.check_password("NewPass123!")