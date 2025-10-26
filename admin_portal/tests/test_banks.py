import pytest
from rest_framework.test import APIClient
from django.contrib.auth import get_user_model

User = get_user_model()

def _login_admin() -> APIClient:
    client = APIClient()
    admin = User.objects.create_user(
        email="admin@example.com",
        password="pass",
        role=User.Role.ADMIN,
        is_staff=True,
        is_active=True,
    )
    r = client.post("/api/auth/login/", {"email":"admin@example.com","password":"pass"}, format="json")
    assert r.status_code == 200
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}")
    return client

@pytest.mark.django_db
def test_bank_crud_happy_path():
    client = _login_admin()

    # Create
    payload = {
        "name": "Acme Bank",
        "contact_person": "Jane Doe",
        "contact_email": "jane@acmebank.com",
        "contact_phone": "9999999999",
        "status": "ACTIVE",
        "notes": "Preferred partner",
    }
    r = client.post("/api/admin/banks/", payload, format="json")
    assert r.status_code == 201, r.content
    bank_id = r.json()["id"]

    # List (no filters)
    lst = client.get("/api/admin/banks/")
    assert lst.status_code == 200
    assert any(b["id"] == bank_id for b in lst.json())

    # Search
    srch = client.get("/api/admin/banks/?q=Acme")
    assert srch.status_code == 200
    assert any(b["id"] == bank_id for b in srch.json())

    # Retrieve
    one = client.get(f"/api/admin/banks/{bank_id}/")
    assert one.status_code == 200
    assert one.json()["name"] == "Acme Bank"

    # Update (status)
    up = client.patch(f"/api/admin/banks/{bank_id}/", {"status": "INACTIVE"}, format="json")
    assert up.status_code == 200 and up.json()["status"] == "INACTIVE"

    # Filter by status
    filt = client.get("/api/admin/banks/?status=INACTIVE")
    assert filt.status_code == 200
    assert any(b["id"] == bank_id for b in filt.json())

    # Delete
    de = client.delete(f"/api/admin/banks/{bank_id}/")
    assert de.status_code == 204

    # Gone
    gone = client.get(f"/api/admin/banks/{bank_id}/")
    assert gone.status_code == 404

@pytest.mark.django_db
def test_unique_name_validation_and_ordering():
    client = _login_admin()

    r1 = client.post("/api/admin/banks/", {"name": "Zeta Bank"}, format="json")
    assert r1.status_code == 201
    r2 = client.post("/api/admin/banks/", {"name": "Alpha Bank"}, format="json")
    assert r2.status_code == 201

    # Unique constraint (same name)
    dup = client.post("/api/admin/banks/", {"name": "Alpha Bank"}, format="json")
    assert dup.status_code in (400, 409)

    # Ordering
    asc = client.get("/api/admin/banks/?ordering=name").json()
    names_asc = [b["name"] for b in asc]
    assert names_asc == sorted(names_asc)

    desc = client.get("/api/admin/banks/?ordering=-name").json()
    names_desc = [b["name"] for b in desc]
    assert names_desc == sorted(names_desc, reverse=True)