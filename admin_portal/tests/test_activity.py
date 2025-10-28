import pytest
from rest_framework.test import APIClient
from accounts.models import User
from admin_portal.models import ActivityLog

@pytest.mark.django_db
@pytest.mark.audit_signals
def test_activity_create_update_delete_and_filters():
    admin = User.objects.create_user(email="admin@x.com", password="pass", role=User.Role.ADMIN, is_staff=True)
    c = APIClient()
    assert c.post("/api/auth/login/", {"email":"admin@x.com","password":"pass"}, format="json").status_code == 200
    token = c.post("/api/auth/login/", {"email":"admin@x.com","password":"pass"}, format="json").data["access"]
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    # CREATE via API
    r1 = c.post("/api/admin/banks/", {"name": "State Bank Alpha", "status": "ACTIVE"}, format="json")
    assert r1.status_code == 201, r1.content
    bank_id = r1.json()["id"]

    # assert CREATE log
    assert ActivityLog.objects.filter(action=ActivityLog.Action.CREATE, model="bank", object_id=str(bank_id)).exists()

    # UPDATE via API
    r2 = c.patch(f"/api/admin/banks/{bank_id}/", {"name": "State Bank Alpha+", "status": "INACTIVE"}, format="json")
    assert r2.status_code == 200, r2.content

    upd = ActivityLog.objects.filter(action=ActivityLog.Action.UPDATE, model="bank", object_id=str(bank_id)).order_by("-created_at").first()
    assert upd and "name" in upd.changes and upd.changes["name"]["from"] == "State Bank Alpha" and upd.changes["name"]["to"] == "State Bank Alpha+"
    assert "changed from" in (upd.help_text or "")

    # API_HIT middleware (this request itself)
    assert ActivityLog.objects.filter(action=ActivityLog.Action.API_HIT, meta__path__icontains="/api/admin/banks/").exists()

    # DELETE
    r3 = c.delete(f"/api/admin/banks/{bank_id}/")
    assert r3.status_code == 204
    assert ActivityLog.objects.filter(action=ActivityLog.Action.DELETE, model="bank", object_id=str(bank_id)).exists()

    # list endpoint + filters
    lst = c.get("/api/admin/audit/?action=UPDATE&q=Alpha+")
    assert lst.status_code == 200
    data = lst.json()
    assert "results" in data and isinstance(data["results"], list)

@pytest.mark.django_db
def test_activity_pagination_and_detail():
    admin = User.objects.create_user(email="admin2@x.com", password="pass", role=User.Role.ADMIN, is_staff=True)
    c = APIClient()
    tok = c.post("/api/auth/login/", {"email":"admin2@x.com","password":"pass"}, format="json").data["access"]
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {tok}")

    # create some logs by hitting endpoints
    for _ in range(5):
        c.get("/api/admin/meta/question-types/")

    r = c.get("/api/admin/audit/?page=1&page_size=2&ordering=-created_at")
    assert r.status_code == 200
    payload = r.json()
    assert payload["page"] == 1 and payload["page_size"] == 2
    assert len(payload["results"]) <= 2

    # detail
    if payload["results"]:
        log_id = payload["results"][0]["id"]
        d = c.get(f"/api/admin/audit/{log_id}/")
        assert d.status_code == 200
        assert "help_text" in d.json()