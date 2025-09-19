import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from pathlib import Path
from rest_framework.test import APIClient
from organizations.models import Organization 

User = get_user_model()

@pytest.fixture
def user(db):
    User = get_user_model()
    return User.objects.create_user(username="tester", password="pass1234")

@pytest.fixture(scope="session", autouse=True)
def seed_questionnaire_db(django_db_setup, django_db_blocker):
    # Ensure DB is ready, then seed from JSON
    json_path = Path("seed/questionnaire_v1.json")
    with django_db_blocker.unblock():
        call_command("seed_questionnaire", "--file", str(json_path))


@pytest.fixture
def api_client(user_with_org):
    client = APIClient()
    client.force_authenticate(user=user_with_org)
    return client, user_with_org

@pytest.fixture
def user_with_org(db):
    """
    Creates a test user and links an Organization via created_by.
    """
    user = User.objects.create_user(
        email="testspo@example.com",
        password="password123",
        role="SPO",
    )
    org = Organization.objects.create(
        name="Test Org",
        registration_type=Organization.RegistrationType.PRIVATE_LTD,
        created_by=user,
    )
    return user