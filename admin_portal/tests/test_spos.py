import pytest
from rest_framework.test import APIClient
from organizations.models import Organization
from accounts.models import User

@pytest.fixture
def admin_client(db):
    admin = User.objects.create_user(email="admin@example.com", password="pass", role=User.Role.ADMIN, is_staff=True)
    c = APIClient()
    login = c.post("/api/auth/login/", {"email": "admin@example.com", "password": "pass"}, format="json")
    assert login.status_code == 200
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
    return c

@pytest.mark.django_db
def test_spo_crud_and_filters(admin_client):
    c = admin_client

    # CREATE
    payload = {
        "email": "spo1@example.com",
        "first_name": "Sam",
        "last_name": "Patel",
        "phone": "9999999999",
        "password": "StrongPass123!",
        "organization": {"name": "Alpha Org", "registration_type": "PRIVATE_LTD"}
    }
    r = c.post("/api/admin/spos/", payload, format="json")
    assert r.status_code == 201
    spo_id = r.json()["id"]
    assert Organization.objects.filter(created_by_id=spo_id).exists()

    # LIST (search + ordering)
    r2 = c.get("/api/admin/spos/?q=Alpha&ordering=email")
    assert r2.status_code == 200
    assert any(x["organization"]["name"] == "Alpha Org" for x in r2.json())

    # RETRIEVE
    r3 = c.get(f"/api/admin/spos/{spo_id}/")
    assert r3.status_code == 200
    assert r3.json()["email"] == "spo1@example.com"

    # UPDATE (user + org)
    r4 = c.patch(f"/api/admin/spos/{spo_id}/", {
        "first_name": "Samir",
        "organization": {"name": "Alpha Org Pvt"}
    }, format="json")
    assert r4.status_code == 200
    assert r4.json()["first_name"] == "Samir"
    assert r4.json()["organization"]["name"] == "Alpha Org Pvt"

    # TOGGLE STATUS
    r5 = c.patch(f"/api/admin/spos/{spo_id}/toggle-status/")
    assert r5.status_code == 200
    assert r5.json()["is_active"] is False

    # FILTER by status
    r6 = c.get("/api/admin/spos/?status=inactive")
    assert r6.status_code == 200
    assert any(x["id"] == spo_id for x in r6.json())

    # DELETE
    r7 = c.delete(f"/api/admin/spos/{spo_id}/")
    assert r7.status_code == 204
    assert not User.objects.filter(pk=spo_id).exists()