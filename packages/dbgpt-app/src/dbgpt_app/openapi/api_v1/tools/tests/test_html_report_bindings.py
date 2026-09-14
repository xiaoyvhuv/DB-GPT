"""Tests for stable object identifiers in generated HTML reports."""

import json
from datetime import datetime, timezone

import pytest

from dbgpt.core.interface.report import (
    ArtifactContent,
    ChartArtifact,
    PythonArtifact,
    SQLArtifact,
)
from dbgpt.core.interface.report_capture import (
    ArtifactCaptureContext,
    InMemoryArtifactSink,
)
from dbgpt_app.openapi.api_v1.tools.html_interpreter import (
    _prepare_presentation_bindings,
    _validate_complete_html_document,
    _validate_evidence_bindings,
    _validate_recommendation_requirement,
    make_html_interpreter,
)


def test_html_bindings_assign_stable_block_and_claim_identifiers() -> None:
    html, bindings = _prepare_presentation_bindings(
        """
        <section data-dbgpt-artifacts="sql-1" data-dbgpt-evidence-label="总 GMV">
          <strong data-dbgpt-artifacts="sql-1"
                  data-dbgpt-evidence-label="R$13.4M">R$13.4M</strong>
        </section>
        """
    )

    assert 'data-dbgpt-block-id="html-block-1"' in html
    assert 'data-dbgpt-claim-id="html-claim-2"' in html
    assert bindings == [
        {
            "target_id": "html-block-1",
            "target_type": "block",
            "label": "总 GMV",
            "artifact_ids": ["sql-1"],
        },
        {
            "target_id": "html-claim-2",
            "target_type": "claim",
            "label": "R$13.4M",
            "artifact_ids": ["sql-1"],
        },
    ]


def test_html_binding_validation_rejects_missing_and_unknown_artifacts() -> None:
    assert "TRACEABILITY_BINDING_REQUIRED" in (
        _validate_evidence_bindings("<section>GMV</section>", ["sql-1"]) or ""
    )


def test_html_validation_requires_requested_actionable_recommendations() -> None:
    missing = _validate_recommendation_requirement(
        "<html><body><h2>经营概览</h2><p>指标说明</p></body></html>", 3
    )

    assert missing is not None
    assert "REPORT_REQUIREMENT_MISSING" in missing
    assert "3" in missing
    assert _validate_recommendation_requirement(
        """
        <html><body><h2>三条可执行经营建议</h2><ol>
        <li>缩短配送时效。</li><li>优化核心品类补货。</li><li>优化支付转化。</li>
        </ol></body></html>
        """,
        3,
    ) is None


def test_html_binding_validation_rejects_chart_bound_to_table() -> None:
    error = _validate_evidence_bindings(
        '<table data-dbgpt-artifacts="sql-1,chart-1"><tr><td>GMV</td></tr></table>',
        ["sql-1", "chart-1"],
        artifact_types={"sql-1": "sql", "chart-1": "chart"},
    )

    assert error is not None
    assert "TRACEABILITY_BINDING_INVALID" in error
    assert "<table>" in error
    assert "TRACEABILITY_BINDING_INVALID" in (
        _validate_evidence_bindings(
            '<section data-dbgpt-artifacts="invented">GMV</section>', ["sql-1"]
        )
        or ""
    )


def test_html_binding_validation_requires_native_chart_figure() -> None:
    error = _validate_evidence_bindings(
        '<section data-dbgpt-artifacts="sql-1">Trend</section>',
        ["sql-1", "chart-1"],
        required_native_chart_artifact_ids=["chart-1"],
        artifact_types={"sql-1": "sql", "chart-1": "chart"},
    )

    assert error is not None
    assert "chart-1" in error
    assert "<figure" in error
    assert (
        _validate_evidence_bindings(
            '<figure data-dbgpt-artifacts="sql-1,chart-1"></figure>',
            ["sql-1", "chart-1"],
            required_native_chart_artifact_ids=["chart-1"],
            artifact_types={"sql-1": "sql", "chart-1": "chart"},
        )
        is None
    )


def test_html_binding_uses_visible_text_when_label_is_omitted() -> None:
    _, bindings = _prepare_presentation_bindings(
        '<strong data-dbgpt-artifacts="sql-1">下降 12.4%</strong>'
    )

    assert bindings[0]["label"] == "下降 12.4%"


def test_complete_html_validation_rejects_truncated_document() -> None:
    assert "HTML_DOCUMENT_INCOMPLETE" in (
        _validate_complete_html_document("<html><body><section>GMV") or ""
    )
    assert _validate_complete_html_document(
        "<html><body><section>GMV</section></body></html>"
    ) is None


@pytest.mark.asyncio
async def test_html_tool_retains_render_and_structured_bindings_in_turn_state(
    tmp_path,
) -> None:
    sink = InMemoryArtifactSink(
        ArtifactCaptureContext(conv_uid="conv-1", run_id="run-1")
    )
    sink.record(
        SQLArtifact(
            artifact_id="sql-1",
            execution=sink.context.execution_reference(action="sql_query"),
            created_at=datetime.now(timezone.utc),
            status="succeeded",
            duration_ms=1,
            sql="SELECT 42 AS gmv",
            columns=["gmv"],
            row_count=1,
            result=ArtifactContent(
                inline={"columns": ["gmv"], "rows": [[42]]}
            ),
        )
    )
    state = {"_report_artifact_sink": sink}

    response = json.loads(
        await make_html_interpreter(state, str(tmp_path))(
            html=(
                '<html><body><section data-dbgpt-artifacts="sql-1" '
                'data-dbgpt-evidence-label="GMV">42</section></body></html>'
            )
        )
    )

    rendered = response["chunks"][1]["content"]
    assert 'data-dbgpt-block-id="html-block-1"' in rendered
    assert state["_report_presentation_bindings"][0]["artifact_ids"] == [
        "sql-1"
    ]
    assert state["_report_presentation"]["content"] == rendered
    assert len(state["_report_presentation"]["sha256"]) == 64
    assert state["_report_presentation"]["title"] == "Report"
    assert "data-dbgpt-evidence-summary" not in rendered


@pytest.mark.asyncio
async def test_html_tool_adds_lineage_to_a_matching_generated_chart_image(tmp_path) -> None:
    sink = InMemoryArtifactSink(
        ArtifactCaptureContext(conv_uid="conv-1", run_id="run-1")
    )
    now = datetime.now(timezone.utc)
    sql = SQLArtifact(
        artifact_id="sql-1",
        execution=sink.context.execution_reference(action="sql_query"),
        created_at=now,
        status="succeeded",
        duration_ms=1,
        sql="SELECT 42 AS gmv",
        columns=["gmv"],
        row_count=1,
        result=ArtifactContent(inline={"columns": ["gmv"], "rows": [[42]]}),
    )
    python = PythonArtifact(
        artifact_id="python-1",
        execution=sink.context.execution_reference(action="code_interpreter"),
        input_artifact_ids=[sql.artifact_id],
        created_at=now,
        status="succeeded",
        duration_ms=1,
        code="plot()",
        exit_code=0,
        stdout="",
        stderr="",
    )
    chart = ChartArtifact(
        artifact_id="chart-1",
        execution=sink.context.execution_reference(action="code_interpreter.image"),
        input_artifact_ids=[python.artifact_id],
        created_at=now,
        status="succeeded",
        duration_ms=0,
        chart_type="image",
        output=ArtifactContent(uri="/images/monthly.png"),
    )
    for artifact in (sql, python, chart):
        sink.record(artifact)

    state = {"_report_artifact_sink": sink}
    response = json.loads(
        await make_html_interpreter(state, str(tmp_path))(
            html=(
                '<html><body><section data-dbgpt-artifacts="sql-1">'
                '<strong data-dbgpt-artifacts="sql-1">42</strong>'
                '</section><img src="/images/monthly.png"></body></html>'
            )
        )
    )

    rendered = response["chunks"][1]["content"]
    assert 'src="/images/monthly.png"' in rendered
    assert 'data-dbgpt-artifacts="sql-1,python-1,chart-1"' in rendered


@pytest.mark.asyncio
async def test_html_tool_does_not_require_unused_native_chart_artifacts(tmp_path) -> None:
    """Only charts explicitly selected for the report need figure containers."""
    sink = InMemoryArtifactSink(
        ArtifactCaptureContext(conv_uid="conv-1", run_id="run-1")
    )
    now = datetime.now(timezone.utc)
    sql = SQLArtifact(
        artifact_id="sql-1",
        execution=sink.context.execution_reference(action="sql_query"),
        created_at=now,
        status="succeeded",
        duration_ms=1,
        sql="SELECT 42 AS gmv",
        columns=["gmv"],
        row_count=1,
        result=ArtifactContent(inline={"columns": ["gmv"], "rows": [[42]]}),
    )
    selected_chart = ChartArtifact(
        artifact_id="chart-selected",
        execution=sink.context.execution_reference(action="chart_query"),
        input_artifact_ids=[sql.artifact_id],
        created_at=now,
        status="succeeded",
        duration_ms=1,
        chart_type="line",
        spec={"renderer": "vis-db-chart"},
        output=ArtifactContent(uri="dbgpt://charts/selected"),
    )
    unused_chart = ChartArtifact(
        artifact_id="chart-unused",
        execution=sink.context.execution_reference(action="chart_query"),
        input_artifact_ids=[sql.artifact_id],
        created_at=now,
        status="succeeded",
        duration_ms=1,
        chart_type="bar",
        spec={"renderer": "vis-db-chart"},
        output=ArtifactContent(uri="dbgpt://charts/unused"),
    )
    for artifact in (sql, selected_chart, unused_chart):
        sink.record(artifact)

    response = json.loads(
        await make_html_interpreter({"_report_artifact_sink": sink}, str(tmp_path))(
            html=(
                '<html><body><section data-dbgpt-artifacts="sql-1">'
                '<strong data-dbgpt-artifacts="sql-1">42</strong></section>'
                '<figure data-dbgpt-artifacts="sql-1,chart-selected"></figure>'
                '</body></html>'
            )
        )
    )

    assert response["chunks"][0]["content"].startswith("✅ HTML")
