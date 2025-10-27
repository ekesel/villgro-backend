# admin_portal/tests/test_dashboard.py
import json
import pytest
from pprint import pformat
from django.utils import timezone
from datetime import timedelta

from rest_framework.test import APIClient

from accounts.models import User
from organizations.models import Organization
from assessments.models import Assessment
from questionnaires.models import Section


# ----------------------
# Helpers (idempotent + diagnostics)
# ----------------------
def _ensure_core_sections():
    """
    Make sure the core sections exist without violating the unique constraint.
    """
    core = [
        ("IMPACT", "Impact", 1),
        ("RISK", "Risk", 2),
        ("RETURN", "Return", 3),
        ("OTHERS", "Others", 4),
    ]
    for code, title, order in core:
        Section.objects.get_or_create(
            code=code,
            defaults={"title": title, "order": order},
        )


def _login_admin() -> APIClient:
    """
    Create/login an admin user and return an authenticated APIClient.
    Idempotent across test re-runs.
    """
    admin_email = "admin.dashboard@example.com"
    admin, _ = User.objects.get_or_create(
        email=admin_email,
        defaults={
            "role": User.Role.ADMIN,
            "is_staff": True,
            "is_active": True,
        },
    )
    # Always set a known password for login
    admin.set_password("pass")
    admin.save(update_fields=["password"])

    client = APIClient()
    login = client.post("/api/auth/login/", {"email": admin_email, "password": "pass"}, format="json")
    if login.status_code != 200:
        pytest.fail(f"Admin login failed: {login.status_code} {login.content!r}")
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
    return client


def _as_json_or_text(resp):
    try:
        return resp.json()
    except Exception:
        # Not JSON; return decoded text fallback
        try:
            return resp.content.decode("utf-8", errors="replace")
        except Exception:
            return resp.content


def _assert_status_ok(resp, where: str):
    if resp.status_code != 200:
        payload = _as_json_or_text(resp)
        pytest.fail(
            f"{where}: expected 200 OK, got {resp.status_code}\n"
            f"Payload:\n{pformat(payload)}"
        )


def _assert_has_keys(mapping, keys, where: str):
    missing = [k for k in keys if k not in mapping]
    if missing:
        pytest.fail(
            f"{where}: missing keys {missing}\n"
            f"Got keys: {list(mapping.keys())}\n"
            f"Full payload:\n{pformat(mapping)}"
        )


# ----------------------
# Tests
# ----------------------
@pytest.mark.django_db
def test_admin_dashboard_summary_window_and_metrics():
    """
    Smoke test for the dashboard summary endpoint.
    - Ensures sections exist (idempotently).
    - Creates a few SPOs/orgs in and out of the filter window.
    - Verifies response shape and that KPI totals are non-negative.
    """
    client = _login_admin()
    _ensure_core_sections()

    now = timezone.now()
    inside = now - timedelta(days=2)
    outside = now - timedelta(days=40)

    # SPO inside window
    spo_in, _ = User.objects.get_or_create(
        email="in1@example.com",
        defaults={
            "role": User.Role.SPO,
            "is_active": True,
            "date_joined": inside,
        },
    )
    if spo_in.date_joined != inside or not spo_in.is_active or spo_in.role != User.Role.SPO:
        spo_in.date_joined = inside
        spo_in.is_active = True
        spo_in.role = User.Role.SPO
        spo_in.save(update_fields=["date_joined", "is_active", "role"])

    org_in, _ = Organization.objects.get_or_create(
        created_by=spo_in,
        defaults={
            "name": "Org In",
            "registration_type": Organization.RegistrationType.PRIVATE_LTD,
        },
    )
    Assessment.objects.get_or_create(organization=org_in)

    # SPO outside window
    spo_out, _ = User.objects.get_or_create(
        email="out1@example.com",
        defaults={
            "role": User.Role.SPO,
            "is_active": True,
            "date_joined": outside,
        },
    )
    if spo_out.date_joined != outside or not spo_out.is_active or spo_out.role != User.Role.SPO:
        spo_out.date_joined = outside
        spo_out.is_active = True
        spo_out.role = User.Role.SPO
        spo_out.save(update_fields=["date_joined", "is_active", "role"])

    Organization.objects.get_or_create(
        created_by=spo_out,
        defaults={
            "name": "Org Out",
            "registration_type": Organization.RegistrationType.PRIVATE_LTD,
        },
    )

    # Call with 7-day window
    resp = client.get("/api/admin/dashboard/summary?days=7")
    _assert_status_ok(resp, "GET /api/admin/dashboard/summary?days=7")
    data = resp.json()

    # Basic shape
    _assert_has_keys(data, ["kpi", "funnel", "sector_distribution", "recent_activity"], "dashboard summary root")

    # KPI existence & non-negative values
    kpi = data["kpi"]
    _assert_has_keys(kpi, ["total_spos", "new_spos", "completion_rate", "loan_requests", "window"], "kpi")
    if not isinstance(kpi["total_spos"], int) or kpi["total_spos"] < 0:
        pytest.fail(f"kpi.total_spos invalid: {kpi['total_spos']}")
    if not isinstance(kpi["new_spos"], int) or kpi["new_spos"] < 0:
        pytest.fail(f"kpi.new_spos invalid: {kpi['new_spos']}")
    if not isinstance(kpi["loan_requests"], int) or kpi["loan_requests"] < 0:
        pytest.fail(f"kpi.loan_requests invalid: {kpi['loan_requests']}")
    if not isinstance(kpi["completion_rate"], (int, float)) or kpi["completion_rate"] < 0:
        pytest.fail(f"kpi.completion_rate invalid: {kpi['completion_rate']}")

    # Funnel keys present (new shape with nested counts and percents)
    funnel = data["funnel"]
    _assert_has_keys(funnel, ["counts", "percents", "denominators"], "funnel")
    counts = funnel["counts"]
    _assert_has_keys(counts,
                     ["registered", "completed_basic_info", "completed_impact", "completed_risk", "completed_return"],
                     "funnel.counts")
    percents = funnel["percents"]
    _assert_has_keys(percents,
                     ["registered", "completed_basic_info", "completed_impact", "completed_risk", "completed_return"],
                     "funnel.percents")

    # Sector distribution is a list of buckets (may be empty)
    if not isinstance(data["sector_distribution"], (list, dict)):
        pytest.fail(f"sector_distribution invalid type: {type(data['sector_distribution'])} -> {data['sector_distribution']}")

    # Recent activity list ordered by recency (if present)
    if not isinstance(data["recent_activity"], list):
        pytest.fail(f"recent_activity invalid type: {type(data['recent_activity'])} -> {data['recent_activity']}")
    if len(data["recent_activity"]) > 1:
        ts = [item.get("timestamp") for item in data["recent_activity"]]
        if not all(isinstance(t, str) and t for t in ts):
            pytest.fail(f"recent_activity timestamps invalid: {pformat(data['recent_activity'])}")
        if ts != sorted(ts, reverse=True):
            pytest.fail(f"recent_activity not sorted desc: {ts}")


@pytest.mark.django_db
def test_admin_dashboard_respects_days_filter_edges():
    """
    Verifies the 'days' filter affects 'new_spos' as expected.
    """
    client = _login_admin()
    _ensure_core_sections()

    now = timezone.now()
    inside = now - timedelta(days=1)
    edge = now - timedelta(days=7)   # boundary day
    outside = now - timedelta(days=30)

    # SPO accounts spread across time
    u_in, _ = User.objects.get_or_create(
        email="in2@example.com",
        defaults={"role": User.Role.SPO, "is_active": True, "date_joined": inside},
    )
    if u_in.date_joined != inside or not u_in.is_active or u_in.role != User.Role.SPO:
        u_in.date_joined = inside
        u_in.is_active = True
        u_in.role = User.Role.SPO
        u_in.save(update_fields=["date_joined", "is_active", "role"])

    u_edge, _ = User.objects.get_or_create(
        email="edge2@example.com",
        defaults={"role": User.Role.SPO, "is_active": True, "date_joined": edge},
    )
    if u_edge.date_joined != edge or not u_edge.is_active or u_edge.role != User.Role.SPO:
        u_edge.date_joined = edge
        u_edge.is_active = True
        u_edge.role = User.Role.SPO
        u_edge.save(update_fields=["date_joined", "is_active", "role"])

    u_out, _ = User.objects.get_or_create(
        email="out2@example.com",
        defaults={"role": User.Role.SPO, "is_active": True, "date_joined": outside},
    )
    if u_out.date_joined != outside or not u_out.is_active or u_out.role != User.Role.SPO:
        u_out.date_joined = outside
        u_out.is_active = True
        u_out.role = User.Role.SPO
        u_out.save(update_fields=["date_joined", "is_active", "role"])

    # Query with 7-day window
    resp = client.get("/api/admin/dashboard/summary?days=7")
    _assert_status_ok(resp, "GET /api/admin/dashboard/summary?days=7")
    new_7 = resp.json()["kpi"]["new_spos"]
    assert new_7 >= 1  # at least the inside user (and maybe the edge user if inclusive)

    # Query with 1-day window — likely counts only the inside user
    resp2 = client.get("/api/admin/dashboard/summary?days=1")
    _assert_status_ok(resp2, "GET /api/admin/dashboard/summary?days=1")
    new_1 = resp2.json()["kpi"]["new_spos"]
    assert 0 <= new_1 <= new_7