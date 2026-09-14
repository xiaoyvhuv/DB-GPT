"""Expose captured execution artifacts to the HTML report author."""

import json
from collections import Counter
from typing import Any, Dict

from dbgpt.agent.resource.tool.base import tool
from dbgpt.core.interface.report import (
    ChartArtifact,
    PythonArtifact,
    ReferenceArtifact,
    SQLArtifact,
)


_MANIFEST_SQL_ROWS = 3
_MANIFEST_TEXT_CHARS = 360


def _compact_sql_preview(value: Any) -> Any:
    """Keep the evidence manifest actionable instead of flooding the report LLM."""
    if not isinstance(value, dict):
        return value
    preview = dict(value)
    rows = preview.get("rows")
    if isinstance(rows, list) and len(rows) > _MANIFEST_SQL_ROWS:
        preview["rows"] = rows[:_MANIFEST_SQL_ROWS]
        preview["rows_omitted"] = len(rows) - _MANIFEST_SQL_ROWS
    return preview


def _artifact_manifest_item(
    artifact: Any, execution_step_map: Dict[str, str] | None = None
) -> Dict[str, Any]:
    """Serialize the discriminated artifact union without cross-type access."""
    item: Dict[str, Any] = {
        "artifact_id": artifact.artifact_id,
        "type": artifact.type,
        "name": artifact.name,
        "status": artifact.status,
        "action": artifact.execution.action,
        "step_id": (execution_step_map or {}).get(
            artifact.execution.step_id, artifact.execution.step_id
        ),
        "input_artifact_ids": list(artifact.input_artifact_ids),
    }
    if isinstance(artifact, SQLArtifact):
        item.update(
            {
                "database": artifact.database,
                "columns": artifact.columns[:25],
                "row_count": artifact.row_count,
                "result_preview": _compact_sql_preview(
                    artifact.result.inline if artifact.result is not None else None
                ),
            }
        )
    elif isinstance(artifact, PythonArtifact):
        item.update(
            {
                "language": artifact.language,
                "exit_code": artifact.exit_code,
                "result_preview": (
                    artifact.result.inline if artifact.result is not None else None
                ),
                "stdout_preview": (artifact.stdout or "")[:_MANIFEST_TEXT_CHARS],
            }
        )
    elif isinstance(artifact, ChartArtifact):
        item.update(
            {
                "chart_type": artifact.chart_type,
                "output_uri": (
                    artifact.output.uri if artifact.output is not None else None
                ),
            }
        )
    elif isinstance(artifact, ReferenceArtifact):
        item.update(
            {
                "reference_type": artifact.reference_type,
                "source_id": artifact.source_id,
                "source_name": artifact.source_name,
                "uri": artifact.uri,
                "locator": artifact.locator,
                "excerpt": (artifact.excerpt or "")[:_MANIFEST_TEXT_CHARS],
            }
        )
    return item


def make_report_evidence_manifest(react_state: Dict[str, Any]):
    @tool(
        description=(
            "Return the executed artifacts that may be cited by a final HTML report. "
            "Call this immediately before html_interpreter when creating an analysis "
            "report. For every KPI, chart, table, or conclusion that uses an artifact, "
            "add data-dbgpt-artifacts=\"artifact-id[,artifact-id]\" to its containing "
            "HTML element and add a concise data-dbgpt-evidence-label. Wrap numeric "
            "claims in strong/span elements so each number is independently clickable. "
            "Never invent an artifact id or bind an unsupported claim. If status is "
            "empty or an error, do not repeat successful analysis work; report the "
            "capture problem."
        )
    )
    async def report_evidence_manifest() -> str:
        """List bounded artifact identifiers captured earlier in this turn."""
        sink = react_state.get("_report_artifact_sink")
        if sink is None:
            return json.dumps(
                {
                    "status": "capture_unavailable",
                    "artifacts": [],
                    "diagnostics": {
                        "message": (
                            "No artifact sink is attached to this turn. Do not rerun "
                            "successful analysis steps."
                        ),
                    },
                },
                ensure_ascii=False,
            )
        try:
            artifacts = sink.snapshot()
        except Exception as error:
            return json.dumps(
                {
                    "status": "manifest_error",
                    "artifacts": [],
                    "diagnostics": {
                        "message": str(error) or type(error).__name__,
                        "capture_run_id": getattr(sink.context, "run_id", None),
                    },
                },
                ensure_ascii=False,
            )

        execution_step_map = react_state.get("_report_execution_step_map", {})
        items = [
            _artifact_manifest_item(artifact, execution_step_map)
            for artifact in artifacts[:100]
        ]
        counts = Counter(artifact.type for artifact in artifacts)
        succeeded = [item for item in items if item.get("status") == "succeeded"]
        chart_items = [item for item in succeeded if item.get("type") == "chart"]
        python_items = [item for item in succeeded if item.get("type") == "python"]
        sql_items = [item for item in succeeded if item.get("type") == "sql"]
        examples = []
        if sql_items:
            sql_id = sql_items[0]["artifact_id"]
            examples.append(
                {
                    "object": "one SQL-backed KPI or table",
                    "html": (
                        f'<section data-dbgpt-artifacts="{sql_id}" '
                        'data-dbgpt-evidence-label="指标名称">'
                        f'<strong data-dbgpt-artifacts="{sql_id}">数值</strong>'
                        "</section>"
                    ),
                }
            )
        if chart_items:
            chart = chart_items[0]
            related_ids = [*chart.get("input_artifact_ids", []), chart["artifact_id"]]
            # A Python artifact may itself declare the SQL inputs that made the
            # chart.  Include those in the figure's *single* local binding.
            for python in python_items:
                if python["artifact_id"] in related_ids:
                    related_ids = [*python.get("input_artifact_ids", []), *related_ids]
            ids = ",".join(dict.fromkeys(related_ids))
            examples.append(
                {
                    "object": "the matching generated chart only",
                    "html": (
                        f'<figure data-dbgpt-artifacts="{ids}" '
                        'data-dbgpt-evidence-label="图表名称">'
                        f'<img src="{chart.get("output_uri") or "图表URL"}"></figure>'
                    ),
                }
            )
        return json.dumps(
            {
                "status": "ok" if artifacts else "empty",
                "artifacts": items,
                "diagnostics": {
                    "capture_run_id": getattr(sink.context, "run_id", None),
                    "captured_count": len(artifacts),
                    "returned_count": len(items),
                    "counts_by_type": dict(sorted(counts.items())),
                    "truncated": len(artifacts) > len(items),
                },
                "usage": (
                    "Use only the compact artifact list above. Make a concise report "
                    "with 3–6 visible KPI/table/chart objects, not one object per "
                    "artifact. Every visible data object needs its own local binding; "
                    "every conclusion number needs its own strong binding. Empty or "
                    "error status is a capture problem; do not repeat successful SQL "
                    "or Python work to manufacture artifacts."
                ),
                "binding_examples": examples,
            },
            ensure_ascii=False,
        )

    return report_evidence_manifest
