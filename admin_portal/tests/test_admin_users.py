import pytest
from rest_framework.test import APIClient
from accounts.models import User


@pytest.mark.django_db
class TestAdminUsersCRUD:
    def _admin(self):
        return User.objects.create_user(
            email="root@x.com", password="Pass123!", role=User.Role.ADMIN, is_staff=True
        )

    def test_admin_users_crud(self):
        admin = self._admin()
        c = APIClient()
        c.force_authenticate(user=admin)

        # create
        payload = {
            "email": "newadmin@x.com",
            "first_name": "Neha",
            "last_name": "Singh",
            "phone": "9999999999",
            "password": "StrongPass123!",
        }
        r_create = c.post("/api/admin/admins/", payload, format="json")
        assert r_create.status_code == 201
        new_id = r_create.data["id"]

        # list
        r_list = c.get("/api/admin/admins/?q=newadmin")
        assert r_list.status_code == 200
        assert any(u["id"] == new_id for u in r_list.data["results"])

        # retrieve
        r_get = c.get(f"/api/admin/admins/{new_id}/")
        assert r_get.status_code == 200
        assert r_get.data["email"] == "newadmin@x.com"

        # partial update (toggle is_active false)
        r_patch = c.patch(f"/api/admin/admins/{new_id}/", {"is_active": False}, format="json")
        assert r_patch.status_code == 200
        assert r_patch.data["is_active"] is False

        # delete
        r_del = c.delete(f"/api/admin/admins/{new_id}/")
        assert r_del.status_code in (200, 204)

    def test_admin_users_requires_admin(self):
        spo = User.objects.create_user(email="spo@x.com", password="x", role=User.Role.SPO)
        c = APIClient()
        c.force_authenticate(user=spo)

        r = c.get("/api/admin/admins/")
        assert r.status_code in (401, 403)