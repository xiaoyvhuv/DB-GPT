"""MySQL bootstrap and upgrade scripts must include report persistence."""

import re
from pathlib import Path

from sqlalchemy.dialects import mysql

from ..models.models import (
    ReportArtifactDependencyEntity,
    ReportArtifactEntity,
    ReportEntity,
    ReportEvidenceEntity,
    ReportRunEntity,
)


def _repository_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        has_project = (parent / "pyproject.toml").is_file()
        has_schema = (parent / "assets/schema").is_dir()
        if has_project and has_schema:
            return parent
    raise AssertionError("repository root not found")


def test_current_mysql_schema_and_upgrade_create_all_report_tables():
    root = _repository_root()
    paths = [
        root / "assets/schema/dbgpt.sql",
        root / "assets/schema/upgrade/v0_8_2/v0.8.2.sql",
        root / "assets/schema/upgrade/v0_8_2/upgrade_to_v0.8.2.sql",
    ]
    entities = (
        ReportEntity,
        ReportRunEntity,
        ReportArtifactEntity,
        ReportArtifactDependencyEntity,
        ReportEvidenceEntity,
    )

    for path in paths:
        sql = path.read_text(encoding="utf-8")
        for entity in entities:
            table = entity.__tablename__
            match = re.search(
                rf"CREATE TABLE IF NOT EXISTS `{table}` \((.*?)\) ENGINE=InnoDB",
                sql,
                flags=re.DOTALL,
            )
            assert match is not None, f"{path} is missing {table}"
            ddl = match.group(1).lower()
            for column in entity.__table__.columns:
                column_type = column.type.compile(dialect=mysql.dialect()).lower()
                column_type = column_type.replace("integer", "int")
                assert f"`{column.name}` {column_type}" in ddl, (
                    f"{path}:{table} has a mismatched {column.name} column"
                )
            for index in entity.__table__.indexes:
                prefix = "unique key" if index.unique else "key"
                columns = ",".join(f"`{column.name}`" for column in index.columns)
                index_ddl = rf"{prefix}\s+`{index.name}`\s+\({columns}\)"
                assert re.search(index_ddl, ddl), (
                    f"{path}:{table} has a mismatched index {index.name}"
                )
