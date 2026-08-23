"""Persistence model contract tests for reports and evidence."""

from sqlalchemy import BigInteger, Integer

from dbgpt_serve.report.models.models import (
    ReportArtifactDependencyEntity,
    ReportArtifactEntity,
    ReportEntity,
    ReportEvidenceEntity,
    ReportRunEntity,
)


def test_report_tables_use_stable_names():
    assert ReportEntity.__tablename__ == "dbgpt_report"
    assert ReportRunEntity.__tablename__ == "dbgpt_report_run"
    assert ReportArtifactEntity.__tablename__ == "dbgpt_report_artifact"
    assert (
        ReportArtifactDependencyEntity.__tablename__
        == "dbgpt_report_artifact_dependency"
    )
    assert ReportEvidenceEntity.__tablename__ == "dbgpt_report_evidence"


def test_entities_use_sqlite_portable_big_integer_primary_keys():
    for entity in (
        ReportEntity,
        ReportRunEntity,
        ReportArtifactEntity,
        ReportArtifactDependencyEntity,
        ReportEvidenceEntity,
    ):
        column_type = entity.__table__.c.id.type
        assert isinstance(column_type, BigInteger)
        assert isinstance(column_type._variant_mapping["sqlite"], Integer)


def test_report_identity_and_revision_constraints_are_declared():
    report_indexes = {index.name: index for index in ReportEntity.__table__.indexes}
    assert report_indexes["uk_report_owner_report"].unique

    run_indexes = {index.name: index for index in ReportRunEntity.__table__.indexes}
    assert run_indexes["uk_report_run_id"].unique
    assert run_indexes["uk_report_run_owner_report_revision"].unique


def test_artifact_dependency_and_evidence_uniqueness_are_declared():
    artifact_indexes = {
        index.name: index for index in ReportArtifactEntity.__table__.indexes
    }
    dependency_indexes = {
        index.name: index for index in ReportArtifactDependencyEntity.__table__.indexes
    }
    evidence_indexes = {
        index.name: index for index in ReportEvidenceEntity.__table__.indexes
    }

    assert artifact_indexes["uk_report_artifact_run_artifact"].unique
    assert dependency_indexes["uk_report_artifact_dependency"].unique
    assert evidence_indexes["uk_report_evidence_ordinal"].unique
