"""Lifecycle and application-registration tests for ReportServe."""

import asyncio
import inspect

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool

from dbgpt.component import SystemApp
from dbgpt.storage.metadata import Model, db
from dbgpt_serve.core.tests.conftest import create_system_app
from dbgpt_serve.report.api import endpoints as endpoints_module
from dbgpt_serve.report.serve import ReportServe

PREFIX = "/api/v1/reports"


@pytest.fixture(autouse=True)
def _isolate_state():
    endpoints_module._reset_endpoints()
    db.init_db(
        "sqlite:///:memory:",
        engine_args={
            "connect_args": {"check_same_thread": False},
            "poolclass": StaticPool,
        },
    )
    db.create_all()
    yield
    endpoints_module._reset_endpoints()


@pytest.fixture()
def system_app() -> SystemApp:
    return create_system_app({})


@pytest.fixture()
def serve(system_app: SystemApp) -> ReportServe:
    instance = ReportServe(system_app)
    instance.init_app(system_app)
    instance.before_start()
    return instance


def test_router_is_mounted_and_service_is_bound(serve, system_app):
    routes = {
        (route.path, method)
        for route in system_app.app.routes
        for method in getattr(route, "methods", set())
    }

    assert (PREFIX + "/", "POST") in routes
    assert (PREFIX + "/{report_id}", "GET") in routes
    assert (PREFIX + "/{report_id}/runs", "GET") in routes
    assert endpoints_module._service_instance is serve.service


def test_on_init_registers_all_report_tables(serve):
    serve.on_init()

    assert {
        "dbgpt_report",
        "dbgpt_report_run",
        "dbgpt_report_artifact",
        "dbgpt_report_artifact_dependency",
        "dbgpt_report_evidence",
    } <= set(Model.metadata.tables)


def test_before_start_resolves_database_manager(serve):
    serve.before_start()
    assert serve._db_manager is not None
    assert serve.service._dao._db_manager is serve._db_manager


def test_stop_unbinds_endpoints(serve, system_app):
    asyncio.run(serve.async_before_stop())

    response = TestClient(system_app.app).get(
        f"{PREFIX}/report-1", headers={"user-id": "alice"}
    )
    assert response.status_code == 503
    assert response.json()["err_code"] == "REPORT_SERVICE_UNAVAILABLE"


def test_report_serve_is_registered_by_the_application():
    from dbgpt_app.initialization import db_model_initialization, serve_initialization

    serve_source = inspect.getsource(serve_initialization.register_serve_apps)

    assert "ReportServe" in serve_source
    assert "/api/v1/reports" in serve_source
    assert any(
        model.__name__ == "ReportEntity" for model in db_model_initialization._MODELS
    )
