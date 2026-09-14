"""Owner-aware report API endpoint tests."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool

from dbgpt.storage.metadata import db
from dbgpt_serve.report.domain import ReportWriteConflictError
from dbgpt_serve.report.models.dao import ReportDao
from dbgpt_serve.report.service import ReportService
from dbgpt_serve.utils.auth import UserRequest, get_user_from_headers

from ..api.endpoints import _reset_endpoints, init_endpoints, router
from .test_dao import _document

PREFIX = "/api/v1/reports"


@pytest.fixture(autouse=True)
def _setup_db():
    db.init_db(
        "sqlite:///:memory:",
        engine_args={
            "connect_args": {"check_same_thread": False},
            "poolclass": StaticPool,
        },
    )
    db.create_all()
    _reset_endpoints()
    yield
    _reset_endpoints()


def _client(owner: str = "alice") -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix=PREFIX)
    init_endpoints(app, ReportService(ReportDao()))
    app.dependency_overrides[get_user_from_headers] = lambda: UserRequest(user_id=owner)
    return TestClient(app)


def _save(client: TestClient) -> dict:
    response = client.post(
        f"{PREFIX}/", json={"document": _document().model_dump(mode="json")}
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["success"] is True
    return payload["data"]


def test_save_and_read_latest_exact_run_and_history():
    client = _client()
    saved = _save(client)

    latest = client.get(f"{PREFIX}/report-1")
    exact = client.get(f"{PREFIX}/report-1/runs/{saved['run_id']}")
    history = client.get(f"{PREFIX}/report-1/runs")

    assert latest.status_code == exact.status_code == history.status_code == 200
    assert latest.json()["data"]["document"]["report_id"] == "report-1"
    assert exact.json()["data"]["run_id"] == saved["run_id"]
    assert history.json()["data"][0]["revision"] == 1


def test_evidence_endpoint_returns_typed_artifact_and_selectors():
    client = _client()
    saved = _save(client)

    response = client.get(
        f"{PREFIX}/report-1/runs/{saved['run_id']}/blocks/metric-gmv/evidence"
    )

    assert response.status_code == 200
    detail = response.json()["data"][0]
    assert detail["artifact"]["type"] == "sql"
    assert detail["evidence"]["target_selector"] == {"metric": "value"}


def test_another_owner_cannot_discover_a_report():
    alice = _client("alice")
    _save(alice)
    bob = _client("bob")

    response = bob.get(f"{PREFIX}/report-1")

    assert response.status_code == 404
    assert response.json()["err_code"] == "REPORT_NOT_FOUND"


def test_missing_block_has_a_stable_error():
    client = _client()
    saved = _save(client)

    response = client.get(
        f"{PREFIX}/report-1/runs/{saved['run_id']}/blocks/missing/evidence"
    )

    assert response.status_code == 404
    assert response.json()["err_code"] == "REPORT_BLOCK_NOT_FOUND"


def test_invalid_protocol_payload_is_rejected_before_service():
    client = _client()

    response = client.post(f"{PREFIX}/", json={"document": {"report_id": "x"}})

    assert response.status_code == 422


def test_unbound_service_fails_closed_with_result_envelope():
    app = FastAPI()
    app.include_router(router, prefix=PREFIX)
    app.dependency_overrides[get_user_from_headers] = lambda: UserRequest(
        user_id="alice"
    )

    response = TestClient(app).get(f"{PREFIX}/report-1")

    assert response.status_code == 503
    assert response.json()["success"] is False
    assert response.json()["err_code"] == "REPORT_SERVICE_UNAVAILABLE"


def test_oversized_authenticated_owner_is_rejected_before_service():
    client = _client("o" * 256)

    response = client.get(f"{PREFIX}/report-1")

    assert response.status_code == 401
    assert response.json()["err_code"] == "REPORT_OWNER_INVALID"


def test_exhausted_concurrent_save_returns_stable_conflict(monkeypatch):
    client = _client()

    def conflict(*_args, **_kwargs):
        raise ReportWriteConflictError("busy")

    monkeypatch.setattr(ReportService, "save", conflict)
    response = client.post(
        f"{PREFIX}/", json={"document": _document().model_dump(mode="json")}
    )

    assert response.status_code == 409
    assert response.json()["err_code"] == "REPORT_WRITE_CONFLICT"
