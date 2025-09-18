import pytest
from rest_framework.test import APIClient

@pytest.mark.django_db
def test_meta_options_endpoint():
    client = APIClient()
    resp = client.get("/api/meta/options")
    assert resp.status_code == 200
    body = resp.json()
    # basic keys
    for key in [
        "registration_types","innovation_types","geo_scopes",
        "focus_sectors","stages","impact_focus","use_of_questionnaire",
        "states","top_states_limit"
    ]:
        assert key in body

    # structure check for choice arrays: [{key, label}]
    assert isinstance(body["registration_types"], list)
    assert "key" in body["registration_types"][0] and "label" in body["registration_types"][0]
    # states should be a list and have at least a few entries
    assert isinstance(body["states"], list)
    assert len(body["states"]) >= 10