"""HTTP contract tests. Skipped automatically when FastAPI is not installed."""

from __future__ import annotations

import time

import pytest

from tests.conftest import fixture_bytes
from tests.support.service_harness import JD, make_service, standard_batch

pytestmark = [pytest.mark.requires_fastapi]

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.fixture
def client(tmp_path):
    from fastapi.testclient import TestClient

    from app.api.main import create_app

    data = fixture_bytes("simple_one_page")
    h = make_service(tmp_path, [standard_batch(data)])
    app = create_app(settings=h.service.settings, service=h.service)
    with TestClient(app) as c:
        c.harness = h  # type: ignore[attr-defined]
        yield c


def _create(client, data=None, name="cv.docx", mime=DOCX, jd=JD):
    data = data or fixture_bytes("simple_one_page")
    return client.post("/api/sessions", files={"file": (name, data, mime)}, data={"job_description": jd, "company_details": "Acme"})


def _wait(client, sid, token, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/sessions/{sid}", headers={"X-Session-Token": token}).json()
        if body["status"] != "analyzing":
            return body
        time.sleep(0.05)
    raise AssertionError("analysis did not finish")


def test_health_and_ready(client):
    assert client.get("/health").json()["status"] == "ok"
    ready = client.get("/ready").json()
    assert ready["checks"]["database"] is True


def test_happy_path_over_http(client):
    created = _create(client)
    assert created.status_code == 202, created.text
    sid, token = created.json()["session_id"], created.json()["session_token"]
    headers = {"X-Session-Token": token}
    session = _wait(client, sid, token)
    assert session["status"] == "awaiting_review"
    migrate = next(p for p in session["proposals"] if p["original_text"] == "Worked on the migration of")
    r = client.put(
        f"/api/sessions/{sid}/proposals/{migrate['edit_id']}/decision",
        headers=headers,
        json={"review_revision": session["review_revision"], "decision": "accepted"},
    )
    assert r.status_code == 200, r.text
    rev = r.json()["review_revision"]
    fin = client.post(f"/api/sessions/{sid}/finalize", headers=headers, json={"review_revision": rev})
    assert fin.status_code == 200, fin.text
    assert fin.json()["decision"] in {"PASS", "REVIEW_REQUIRED"}
    fid = fin.json()["finalization_id"]
    if fin.json()["decision"] == "REVIEW_REQUIRED":
        client.post(f"/api/sessions/{sid}/finalizations/{fid}/acknowledge", headers=headers)
    link = client.post(f"/api/sessions/{sid}/finalizations/{fid}/download-link", headers=headers)
    assert link.status_code == 200, link.text
    dl = client.get(link.json()["url"])
    assert dl.status_code == 200 and dl.headers["content-type"] == DOCX
    assert "attachment" in dl.headers["content-disposition"]
    assert dl.headers["cache-control"] == "no-store"


def test_error_envelope_and_no_internals(client):
    r = _create(client, data=b"%PDF-1.4", name="cv.pdf", mime="application/pdf")
    assert r.status_code == 422
    body = r.json()["error"]
    assert body["code"] == "upload_rejected" and body["correlation_id"]
    assert "Traceback" not in r.text and "/home" not in r.text and "storage" not in r.text


def test_empty_feedback_not_required_and_validation_errors_enveloped(client):
    r = client.post("/api/sessions", data={"job_description": "x"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"


def test_unknown_session_and_wrong_token_are_404(client):
    created = _create(client).json()
    sid = created["session_id"]
    assert client.get(f"/api/sessions/{sid}").status_code == 404
    assert client.get(f"/api/sessions/{sid}", headers={"X-Session-Token": "wrong"}).status_code == 404
    assert client.get("/api/sessions/" + "0" * 32, headers={"X-Session-Token": created["session_token"]}).status_code == 404


def test_stale_revision_is_409(client):
    created = _create(client).json()
    sid, token = created["session_id"], created["session_token"]
    session = _wait(client, sid, token)
    eid = session["proposals"][0]["edit_id"]
    headers = {"X-Session-Token": token}
    client.put(
        f"/api/sessions/{sid}/proposals/{eid}/decision",
        headers=headers,
        json={"review_revision": session["review_revision"], "decision": "rejected"},
    )
    r = client.put(
        f"/api/sessions/{sid}/proposals/{eid}/decision",
        headers=headers,
        json={"review_revision": session["review_revision"], "decision": "accepted"},
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "stale_review"


def test_oversized_request_is_413(client):
    big = b"0" * (client.harness.service.settings.max_upload_bytes + 2 * 1024 * 1024)
    r = client.post("/api/sessions", files={"file": ("cv.docx", big, DOCX)}, data={"job_description": "x"})
    assert r.status_code == 413


def test_invalid_download_token_is_401(client):
    assert client.get("/api/downloads/abc.def").status_code == 401


def test_shared_mode_requires_api_token(tmp_path):
    from fastapi.testclient import TestClient

    from app.api.main import create_app

    h = make_service(
        tmp_path, [], renderer=False, RESUME_DEPLOYMENT_MODE="shared", RESUME_API_AUTH_TOKEN="t" * 32, RESUME_SIGNING_SECRET="s" * 40
    )
    with TestClient(create_app(settings=h.service.settings, service=h.service)) as c:
        assert c.get("/api/sessions/" + "a" * 32).status_code == 401
        assert c.get("/api/sessions/" + "a" * 32, headers={"Authorization": "Bearer " + "t" * 32}).status_code == 404
        assert c.get("/health").status_code == 200
        assert c.get("/docs").status_code == 404
