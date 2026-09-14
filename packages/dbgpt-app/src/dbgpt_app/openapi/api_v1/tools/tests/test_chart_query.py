import json
from pathlib import Path

import pytest

from dbgpt.agent.core.action.base import ActionOutput
from dbgpt.core.interface.report_capture import (
    ArtifactCaptureContext,
    InMemoryArtifactSink,
    bind_artifact_sink,
)
from dbgpt_app.openapi.api_v1.tools.chart_query import make_chart_query


def test_agentic_prompt_has_no_native_chart_mandate() -> None:
    source = (
        Path(__file__).parents[2] / "agentic_data_api.py"
    ).read_text(encoding="utf-8")

    assert "make_native_chart" not in source
    assert "For a native_chart result" not in source
    assert "use `native_chart` for standard SQL-backed" not in source


class _Connector:
    _db_name = "olist"
    db_type = "sqlite"
    dialect = "sqlite"

    def get_current_db_name(self):
        return self._db_name


@pytest.mark.asyncio
async def test_chart_query_delegates_to_native_chart_action(monkeypatch) -> None:
    calls = []

    class _Action:
        def init_resource(self, resource):
            self.resource = resource

        async def run(self, payload):
            calls.append((self.resource, json.loads(payload)))
            return ActionOutput(
                is_exe_success=True,
                content='{}',
                view='```vis-db-chart\n{"type":"response_line_chart","data":[]}\n```',
            )

    monkeypatch.setattr(
        'dbgpt_app.openapi.api_v1.tools.chart_query.ChartAction', _Action
    )
    sink = InMemoryArtifactSink(ArtifactCaptureContext(conv_uid='conv-1', run_id='run-1'))
    chart_query = make_chart_query({'_report_artifact_sink': sink}, _Connector())

    with bind_artifact_sink(sink):
        response = json.loads(
            await chart_query(
                sql='SELECT month, gmv FROM monthly_gmv',
                display_type='response_line_chart',
                thought='展示月度 GMV 趋势',
            )
        )

    resource, payload = calls[0]
    assert resource._db_name == 'olist'
    assert payload == {
        'sql': 'SELECT month, gmv FROM monthly_gmv',
        'display_type': 'response_line_chart',
        'thought': '展示月度 GMV 趋势',
    }
    assert response['chunks'][0] == {
        'output_type': 'markdown',
        'content': '```vis-db-chart\n{"type":"response_line_chart","data":[]}\n```',
    }
