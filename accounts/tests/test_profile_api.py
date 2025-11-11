import pytest
from rest_framework.test import APIClient
from organizations.models import Organization
from accounts.models import User

@pytest.mark.django_db
def test_profile_get_and_update():
    user = User.objects.create_user(
        email="yakshi@org.com", password="StrongPass123!", first_name="Yakshi", last_name="Agarwal", role=User.Role.SPO
    )
    org = Organization.objects.create(
        name="OrgName",
        registration_type=Organization.RegistrationType.PRIVATE_LTD,
        created_by=user
    )
    client = APIClient()
    login = client.post("/api/auth/login/", {"email":"yakshi@org.com","password":"StrongPass123!"}, format="json")
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")

    # GET profile
    r1 = client.get("/api/profile")
    assert r1.status_code == 200
    assert r1.data["user"]["email"] == "yakshi@org.com"
    assert r1.data["organization"]["name"] == "OrgName"

    # PATCH profile (update names/phone + org legal basics)
    r2 = client.patch("/api/profile", {
        "first_name": "Yukti",
        "last_name": "Agarwal",
        "phone": "+91 9876543210",
        "org_name": "New Org Name",
        "gst_number": "27ABCDE1234F1Z5"
    }, format="json")
    assert r2.status_code == 200
    assert r2.data["user"]["first_name"] == "Yukti"
    assert r2.data["user"]["phone"] == "+91 9876543210"
    assert r2.data["organization"]["name"] == "New Org Name"
    assert r2.data["organization"]["gst_number"] == "27ABCDE1234F1Z5"