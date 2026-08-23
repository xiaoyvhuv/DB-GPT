"""Transactional DAO tests for immutable report runs."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier, Lock

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import StaticPool

from dbgpt.core.interface.report import (
    ArtifactContent,
    ChartArtifact,
    ChartBlock,
    EvidenceReference,
    ExecutionReference,
    MetricBlock,
    ReportDocument,
    ReportSection,
    SQLArtifact,
)
from dbgpt.storage.metadata import db

from ..domain import ReportWriteConflictError
from ..models.dao import ReportDao
from ..models.models import (
    ReportArtifactDependencyEntity,
    ReportArtifactEntity,
    ReportEntity,
    ReportEvidenceEntity,
    ReportRunEntity,
)


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


def _document(*, title: str = "Sales", conv_uid: str = "conv-1") -> ReportDocument:
    now = datetime(2026, 8, 23, tzinfo=timezone.utc)
    sql = SQLArtifact(
        artifact_id="sql-1",
        execution=ExecutionReference(conv_uid=conv_uid, step_id="step-sql"),
        created_at=now,
        sql="select sum(gmv) as gmv from orders",
        columns=["gmv"],
        row_count=1,
        result=ArtifactContent(inline=[{"gmv": 42}]),
    )
    chart = ChartArtifact(
        artifact_id="chart-1",
        execution=ExecutionReference(conv_uid=conv_uid, step_id="step-chart"),
        input_artifact_ids=["sql-1"],
        created_at=now,
        chart_type="bar",
        output=ArtifactContent(uri="dbgpt://reports/chart-1.png"),
    )
    evidence = EvidenceReference(
        artifact_id="sql-1",
        role="calculation",
        selector={"row": 0, "column": "gmv"},
        target_selector={"metric": "value"},
    )
    return ReportDocument(
        report_id="report-1",
        title=title,
        conv_uid=conv_uid,
        created_at=now,
        sections=[
            ReportSection(
                section_id="summary",
                title="Summary",
                blocks=[
                    MetricBlock(block_id="metric-gmv", value=42, evidence=[evidence]),
                    ChartBlock(
                        block_id="chart-gmv",
                        chart_artifact_id="chart-1",
                        evidence=[evidence],
                    ),
                ],
            )
        ],
        artifacts=[sql, chart],
    )


def test_save_creates_lossless_snapshot_and_normalized_indexes():
    dao = ReportDao()
    document = _document()

    saved = dao.save(document, owner_id="alice", run_id="run-1")

    assert saved.run_id == "run-1"
    assert saved.revision == 1
    assert saved.document == document
    with dao.session(commit=False) as session:
        assert session.query(ReportEntity).count() == 1
        assert session.query(ReportRunEntity).count() == 1
        assert session.query(ReportArtifactEntity).count() == 2
        assert session.query(ReportArtifactDependencyEntity).count() == 1
        assert session.query(ReportEvidenceEntity).count() == 2


def test_save_appends_revision_and_latest_read_changes_without_mutating_old_run():
    dao = ReportDao()
    first = dao.save(_document(), owner_id="alice", run_id="run-1")
    second = dao.save(
        _document(title="Sales revised"), owner_id="alice", run_id="run-2"
    )

    assert (first.revision, second.revision) == (1, 2)
    assert dao.get_latest("alice", "report-1") == second
    assert dao.get_run("alice", "report-1", "run-1") == first
    assert [run.run_id for run in dao.list_runs("alice", "report-1")] == [
        "run-2",
        "run-1",
    ]


def test_reads_are_owner_scoped():
    dao = ReportDao()
    dao.save(_document(), owner_id="alice", run_id="run-1")

    assert dao.get_latest("bob", "report-1") is None
    assert dao.get_run("bob", "report-1", "run-1") is None
    assert dao.list_runs("bob", "report-1") == []
    assert dao.get_block_evidence("bob", "report-1", "run-1", "metric-gmv") == []


def test_evidence_lookup_returns_ordered_bindings_and_artifacts():
    dao = ReportDao()
    dao.save(_document(), owner_id="alice", run_id="run-1")

    details = dao.get_block_evidence("alice", "report-1", "run-1", "metric-gmv")

    assert len(details) == 1
    assert details[0].section_id == "summary"
    assert details[0].block_id == "metric-gmv"
    assert details[0].evidence.artifact_id == "sql-1"
    assert details[0].artifact.type == "sql"
    assert details[0].artifact.sql.startswith("select")


def test_report_conversation_identity_cannot_change_between_revisions():
    dao = ReportDao()
    dao.save(_document(), owner_id="alice", run_id="run-1")

    with pytest.raises(ValueError, match="conv_uid"):
        dao.save(
            _document(conv_uid="conv-other"),
            owner_id="alice",
            run_id="run-2",
        )

    assert [run.run_id for run in dao.list_runs("alice", "report-1")] == ["run-1"]


def test_parent_run_must_belong_to_the_same_owner_scoped_report():
    dao = ReportDao()
    dao.save(_document(), owner_id="alice", run_id="run-1")

    with pytest.raises(ValueError, match="parent_run_id"):
        dao.save(
            _document(),
            owner_id="alice",
            run_id="run-2",
            parent_run_id="missing-run",
        )

    assert [run.run_id for run in dao.list_runs("alice", "report-1")] == ["run-1"]


def test_failed_save_rolls_back_new_report_and_indexes():
    dao = ReportDao()
    dao.save(_document(), owner_id="alice", run_id="duplicate-run")

    other = _document()
    other.report_id = "report-2"
    with pytest.raises(ValueError, match="run_id"):
        dao.save(other, owner_id="bob", run_id="duplicate-run")

    assert dao.get_latest("bob", "report-2") is None


def test_save_revalidates_a_mutated_protocol_instance_before_writing():
    dao = ReportDao()
    document = _document()
    document.sections[0].blocks[0].evidence[0].artifact_id = "missing"

    with pytest.raises(ValueError, match="unknown artifact"):
        dao.save(document, owner_id="alice", run_id="run-invalid")

    assert dao.get_latest("alice", "report-1") is None


def test_save_rejects_a_malformed_mutated_nested_model_without_attribute_error():
    dao = ReportDao()
    document = _document()
    document.sections[0] = {}  # type: ignore[assignment]

    with pytest.raises(ValueError, match="ReportDocument"):
        dao.save(document, owner_id="alice", run_id="run-invalid")

    assert dao.get_latest("alice", "report-1") is None


@pytest.mark.parametrize("non_finite", [float("nan"), float("inf"), -float("inf")])
def test_save_rejects_non_finite_json_without_writing(non_finite):
    dao = ReportDao()
    document = _document()
    document.sections[0].blocks[0].evidence[0].selector["score"] = non_finite

    with pytest.raises(ValueError, match="JSON"):
        dao.save(document, owner_id="alice", run_id="run-invalid")

    assert dao.get_latest("alice", "report-1") is None


def _replace_sql_artifact_id(document):
    artifact_id = "a" * 129
    document.artifacts[0].artifact_id = artifact_id
    document.artifacts[1].input_artifact_ids[0] = artifact_id
    for block in document.sections[0].blocks:
        block.evidence[0].artifact_id = artifact_id


@pytest.mark.parametrize(
    ("field_name", "mutate"),
    [
        ("report_id", lambda doc: setattr(doc, "report_id", "r" * 65)),
        ("title", lambda doc: setattr(doc, "title", "t" * 513)),
        ("conv_uid", lambda doc: setattr(doc, "conv_uid", "c" * 256)),
        (
            "section_id",
            lambda doc: setattr(doc.sections[0], "section_id", "s" * 129),
        ),
        (
            "block_id",
            lambda doc: setattr(doc.sections[0].blocks[0], "block_id", "b" * 129),
        ),
        ("artifact_id", _replace_sql_artifact_id),
        (
            "execution.conv_uid",
            lambda doc: setattr(doc.artifacts[0].execution, "conv_uid", "c" * 256),
        ),
        (
            "execution.step_id",
            lambda doc: setattr(doc.artifacts[0].execution, "step_id", "s" * 256),
        ),
        (
            "execution.message_id",
            lambda doc: setattr(doc.artifacts[0].execution, "message_id", "m" * 256),
        ),
        (
            "execution.task_id",
            lambda doc: setattr(doc.artifacts[0].execution, "task_id", "t" * 65),
        ),
        (
            "execution.run_id",
            lambda doc: setattr(doc.artifacts[0].execution, "run_id", "r" * 65),
        ),
    ],
)
def test_save_rejects_values_longer_than_mysql_columns(field_name, mutate):
    dao = ReportDao()
    document = _document()
    mutate(document)

    with pytest.raises(ValueError, match=field_name):
        dao.save(document, owner_id="alice", run_id="run-invalid")

    assert dao.get_latest("alice", document.report_id) is None


@pytest.mark.parametrize(
    ("kwargs", "field_name"),
    [
        ({"owner_id": "o" * 256, "run_id": "run-1"}, "owner_id"),
        ({"owner_id": "alice", "run_id": "r" * 65}, "run_id"),
        (
            {
                "owner_id": "alice",
                "run_id": "run-1",
                "parent_run_id": "p" * 65,
            },
            "parent_run_id",
        ),
    ],
)
def test_save_rejects_oversized_scope_and_run_identifiers(kwargs, field_name):
    with pytest.raises(ValueError, match=field_name):
        ReportDao().save(_document(), **kwargs)


def _install_two_writer_barrier(monkeypatch):
    barrier = Barrier(2)
    lock = Lock()
    calls = 0

    def before_flush(*_args):
        nonlocal calls
        with lock:
            calls += 1
            should_wait = calls <= 2
        if should_wait:
            barrier.wait(timeout=5)

    monkeypatch.setattr(ReportDao, "_before_flush", before_flush)


def test_concurrent_first_saves_allocate_distinct_revisions(tmp_path, monkeypatch):
    db.init_db(
        f"sqlite:///{(tmp_path / 'reports.db').as_posix()}",
        engine_args={"connect_args": {"check_same_thread": False, "timeout": 5}},
    )
    db.create_all()
    _install_two_writer_barrier(monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                ReportDao().save,
                _document(title=f"Sales {number}"),
                owner_id="alice",
                run_id=f"run-{number}",
            )
            for number in (1, 2)
        ]
        records = [future.result(timeout=10) for future in futures]

    assert {record.revision for record in records} == {1, 2}
    assert len(ReportDao().list_runs("alice", "report-1")) == 2


def test_concurrent_appends_allocate_distinct_revisions(tmp_path, monkeypatch):
    db.init_db(
        f"sqlite:///{(tmp_path / 'reports.db').as_posix()}",
        engine_args={"connect_args": {"check_same_thread": False, "timeout": 5}},
    )
    db.create_all()
    ReportDao().save(_document(), owner_id="alice", run_id="run-1")
    _install_two_writer_barrier(monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                ReportDao().save,
                _document(title=f"Sales {number}"),
                owner_id="alice",
                run_id=f"run-{number}",
            )
            for number in (2, 3)
        ]
        records = [future.result(timeout=10) for future in futures]

    assert {record.revision for record in records} == {2, 3}
    assert len(ReportDao().list_runs("alice", "report-1")) == 3


def test_mysql_deadlock_retries_are_bounded_and_sanitized(monkeypatch):
    attempts = 0

    def deadlock(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise OperationalError("insert", {}, Exception(1213, "Deadlock found"))

    monkeypatch.setattr(ReportDao, "_save_once", deadlock)

    with pytest.raises(ReportWriteConflictError):
        ReportDao().save(_document(), owner_id="alice", run_id="run-1")

    assert attempts == 3
