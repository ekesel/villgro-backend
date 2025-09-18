import pytest
from rest_framework.test import APIClient

@pytest.mark.django_db
def test_swagger_schema_accessible():
    client = APIClient()
    response = client.get("/api/schema/")
    assert response.status_code == 200
    assert "openapi" in response.json()