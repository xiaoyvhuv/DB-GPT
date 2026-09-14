"""Tests for SQL/chart evidence captured by the native ChartAction."""

from datetime import datetime, timezone
from decimal import Decimal

import pandas as pd
import pytest

from dbgpt.agent.expand.actions.chart_action import ChartAction, SqlInput
from dbgpt.agent.resource.database import DBResource
from dbgpt.core.interface.report import ChartArtifact, SQLArtifact
from dbgpt.core.interface.report_capture import (
    ArtifactCaptureContext,
    InMemoryArtifactSink,
    bind_artifact_sink,
    sha256_json,
    sha256_text,
)


class _Database:
    _db_name = "sales"

    async def query_to_df(self, sql: str):
        return pd.DataFrame([{"day": "2026-08-23", "gmv": 100}])


class _Renderer:
    async def display(self, *, chart, data_df):
        return '{"type":"line","data":[100]}'


class _NativeVisRenderer:
    async def display(self, *, chart, data_df):
        return (
            "```vis-db-chart\n"
            '{"type":"response_line_chart","title":"GMV trend",'
            '"describe":"monthly GMV","sql":"SELECT day, gmv FROM sales",'
            '"data":[{"day":"2026-08-23","gmv":100}]}\n'
            "```"
        )


def _action(monkeypatch, renderer=None, database=None) -> ChartAction:
    action = ChartAction()
    action._render_protocol = renderer or _Renderer()
    monkeypatch.setattr(
        action,
        "_input_convert",
        lambda *_args: SqlInput(
            display_type="line",
            sql="SELECT day, gmv FROM sales",
            thought="show trend",
        ),
    )
    monkeypatch.setattr(
        DBResource, "from_resource", lambda _resource: [database or _Database()]
    )
    return action


def _sink() -> InMemoryArtifactSink:
    return InMemoryArtifactSink(
        ArtifactCaptureContext(conv_uid="conv-1", run_id="run-1")
    )


@pytest.mark.asyncio
async def test_chart_action_captures_sql_to_chart_dependency(monkeypatch) -> None:
    action = _action(monkeypatch)
    sink = _sink()

    with bind_artifact_sink(sink):
        result = await action.run("ignored")

    sql, chart = sink.snapshot()
    assert result.is_exe_success
    assert isinstance(sql, SQLArtifact)
    assert sql.result is not None
    assert sql.result.inline == {
        "columns": ["day", "gmv"],
        "rows": [["2026-08-23", 100]],
    }
    assert isinstance(chart, ChartArtifact)
    assert chart.input_artifact_ids == [sql.artifact_id]
    assert sql.timing is not None
    assert sql.timing.server_execution_ms == sql.duration_ms
    assert chart.timing is not None
    assert chart.timing.server_execution_ms == chart.duration_ms
    assert chart.timing.browser_render_state == "not_measured"
    assert chart.output is not None
    assert chart.output.inline == result.view
    assert chart.output.sha256 == sha256_text(result.view)


@pytest.mark.asyncio
async def test_chart_action_captures_native_vis_chart_spec(monkeypatch) -> None:
    action = _action(monkeypatch, renderer=_NativeVisRenderer())
    sink = _sink()

    with bind_artifact_sink(sink):
        result = await action.run("ignored")

    sql, chart = sink.snapshot()
    assert result.is_exe_success
    assert isinstance(chart, ChartArtifact)
    assert chart.input_artifact_ids == [sql.artifact_id]
    assert chart.spec == {
        "renderer": "vis-db-chart",
        "type": "response_line_chart",
        "title": "GMV trend",
        "describe": "monthly GMV",
        "sql": "SELECT day, gmv FROM sales",
        "data": [{"day": "2026-08-23", "gmv": 100}],
    }


@pytest.mark.asyncio
async def test_chart_action_keeps_legacy_spec_for_invalid_vis_payload(monkeypatch) -> None:
    action = _action(monkeypatch, renderer=_Renderer())
    sink = _sink()

    with bind_artifact_sink(sink):
        await action.run("ignored")

    chart = sink.snapshot()[-1]
    assert isinstance(chart, ChartArtifact)
    assert chart.spec == {"display_type": "line"}


@pytest.mark.asyncio
async def test_chart_action_preserves_precise_values_and_bounds_preview(
    monkeypatch,
) -> None:
    precise_time = datetime(2026, 8, 23, 12, 0, 0, 123456, tzinfo=timezone.utc)

    class _PreciseDatabase:
        _db_name = "sales"

        async def query_to_df(self, sql: str):
            return pd.DataFrame(
                {
                    "amount": [Decimal("1.2300")] * 150,
                    "observed_at": [precise_time] * 150,
                    "special": [float("nan")] * 150,
                    "payload": ["x" * 5_000] * 150,
                }
            )

    action = _action(monkeypatch, database=_PreciseDatabase())
    sink = _sink()

    with bind_artifact_sink(sink):
        result = await action.run("ignored")

    assert result.is_exe_success
    sql = sink.snapshot()[0]
    assert isinstance(sql, SQLArtifact)
    assert sql.row_count == 150
    assert sql.result is not None
    assert sql.result.inline["truncated"] is True
    assert sql.result.size < 300_000
    first = sql.result.inline["rows"][0]
    assert first[0] == {"$decimal": "1.2300"}
    assert first[1] == {"$datetime": precise_time.isoformat()}
    assert first[2] == {"$number": "NaN"}
    full = {
        "columns": ["amount", "observed_at", "special", "payload"],
        "rows": [first] * 150,
    }
    assert sql.metadata["output_summary"]["complete_result_sha256"] == sha256_json(full)


@pytest.mark.asyncio
async def test_chart_action_preserves_duplicate_column_positions(monkeypatch) -> None:
    class _DuplicateColumnsDatabase:
        _db_name = "sales"

        async def query_to_df(self, sql: str):
            return pd.DataFrame([[1, 2]], columns=["id", "id"])

    action = _action(monkeypatch, database=_DuplicateColumnsDatabase())
    sink = _sink()

    with bind_artifact_sink(sink):
        result = await action.run("ignored")

    # Existing ChartAction response serialization rejects duplicate columns,
    # but capture occurs first and must still preserve every cell by position.
    assert not result.is_exe_success
    sql = sink.snapshot()[0]
    assert isinstance(sql, SQLArtifact)
    assert sql.columns == ["id", "id"]
    assert sql.result is not None
    assert sql.result.inline == {"columns": ["id", "id"], "rows": [[1, 2]]}


@pytest.mark.asyncio
async def test_chart_action_captures_renderer_failure_without_fake_output(
    monkeypatch,
) -> None:
    class _FailingRenderer:
        async def display(self, *, chart, data_df):
            raise RuntimeError("renderer unavailable")

    action = _action(monkeypatch, _FailingRenderer())
    sink = _sink()

    with bind_artifact_sink(sink):
        result = await action.run("ignored")

    sql, chart = sink.snapshot()
    assert not result.is_exe_success
    assert isinstance(sql, SQLArtifact)
    assert isinstance(chart, ChartArtifact)
    assert chart.status == "failed"
    assert chart.error == "renderer unavailable"
    assert chart.output is None
    assert chart.input_artifact_ids == [sql.artifact_id]


@pytest.mark.asyncio
async def test_chart_action_bounds_large_render_but_hashes_complete_view(
    monkeypatch,
) -> None:
    view = "v" * 100_000

    class _LargeRenderer:
        async def display(self, *, chart, data_df):
            return view

    action = _action(monkeypatch, _LargeRenderer())
    sink = _sink()

    with bind_artifact_sink(sink):
        result = await action.run("ignored")

    assert result.is_exe_success
    chart = sink.snapshot()[1]
    assert isinstance(chart, ChartArtifact)
    assert chart.output is not None
    assert chart.output.inline == view[:50_000]
    assert chart.metadata["output_summary"] == {
        "complete_view_chars": len(view),
        "complete_view_sha256": sha256_text(view),
        "preview_truncated": True,
    }


@pytest.mark.asyncio
async def test_chart_action_captures_query_failure_without_chart(monkeypatch) -> None:
    class _FailingDatabase:
        _db_name = "sales"

        async def query_to_df(self, sql: str):
            raise RuntimeError("database unavailable")

    action = _action(monkeypatch, database=_FailingDatabase())
    sink = _sink()

    with bind_artifact_sink(sink):
        result = await action.run("ignored")

    artifacts = sink.snapshot()
    assert not result.is_exe_success
    assert len(artifacts) == 1
    assert isinstance(artifacts[0], SQLArtifact)
    assert artifacts[0].status == "failed"
    assert artifacts[0].error == "database unavailable"
    assert artifacts[0].result is None
