"""Tests for report artifacts captured at real tool execution boundaries."""

import json
from pathlib import Path
from typing import Optional

import pytest

from dbgpt.core.interface.report import ChartArtifact, PythonArtifact, SQLArtifact
from dbgpt.core.interface.report_capture import (
    ArtifactCaptureContext,
    InMemoryArtifactSink,
    bind_artifact_sink,
    sha256_json,
)
from dbgpt_app.openapi.api_v1.subagent.react_tools import make_react_tools
from dbgpt_app.openapi.api_v1.tools import code_interpreter as code_interpreter_module
from dbgpt_app.openapi.api_v1.tools.artifact_capture import MAX_SQL_PREVIEW_ROWS
from dbgpt_app.openapi.api_v1.tools.sql_query import (
    _normalize_sql_markdown_escapes,
    make_sql_query,
)


class _SQLConnector:
    def __init__(self, result=None, error: Optional[Exception] = None):
        self._result = result
        self._error = error
        self._db_name = "sales"

    def run(self, sql: str):
        if self._error is not None:
            raise self._error
        return self._result


def test_sql_normalizes_only_markdown_escape_characters() -> None:
    assert _normalize_sql_markdown_escapes(
        r"SELECT order\_id, '\d+' AS pattern, `x\_y`"
    ) == r"SELECT order_id, '\d+' AS pattern, `x_y`"


def _sink() -> InMemoryArtifactSink:
    return InMemoryArtifactSink(
        ArtifactCaptureContext(conv_uid="conv-1", run_id="run-1")
    )


def test_sql_tool_captures_complete_result_before_display_truncation() -> None:
    rows = [(index, "x" * 500) for index in range(55)]
    connector = _SQLConnector(result=[[("id",), ("payload",)], *rows])
    sink = _sink()
    tool = make_sql_query({"conv_id": "conv-1"}, connector)

    with bind_artifact_sink(sink):
        response = json.loads(tool(sql="SELECT id, payload FROM sales"))

    artifact = sink.snapshot()[0]
    assert isinstance(artifact, SQLArtifact)
    assert artifact.status == "succeeded"
    assert artifact.duration_ms is not None
    assert artifact.timing is not None
    assert artifact.timing.server_execution_ms == artifact.duration_ms
    assert artifact.timing.derivative_processing_state == "not_applicable"
    assert artifact.timing.browser_render_state == "not_applicable"
    assert artifact.database == "sales"
    assert artifact.columns == ["id", "payload"]
    assert artifact.row_count == 55
    assert artifact.result is not None
    assert artifact.result.inline == {
        "columns": ["id", "payload"],
        "rows": [[index, "x" * 500] for index in range(55)],
    }
    assert artifact.result.sha256 == sha256_json(artifact.result.inline)
    assert "Output truncated at 20000 chars" in response["chunks"][0]["content"]


def test_sql_tool_captures_empty_and_failed_executions() -> None:
    empty_sink = _sink()
    with bind_artifact_sink(empty_sink):
        empty_response = make_sql_query(
            {"conv_id": "conv-1"}, _SQLConnector(result=[])
        )(sql="SELECT id FROM sales WHERE 1 = 0")

    empty = empty_sink.snapshot()[0]
    assert isinstance(empty, SQLArtifact)
    assert empty.status == "succeeded"
    assert empty.row_count == 0
    assert empty.result is not None
    assert empty.result.inline == {"columns": [], "rows": []}
    assert "查询返回空结果" in empty_response

    failed_sink = _sink()
    with bind_artifact_sink(failed_sink):
        failed_response = make_sql_query(
            {"conv_id": "conv-1"},
            _SQLConnector(error=RuntimeError("database unavailable")),
        )(sql="SELECT id FROM sales")

    failed = failed_sink.snapshot()[0]
    assert isinstance(failed, SQLArtifact)
    assert failed.status == "failed"
    assert failed.result is None
    assert failed.error == "database unavailable"
    assert "SQL 执行失败: database unavailable" in failed_response


def test_sql_tool_without_bound_sink_keeps_existing_output() -> None:
    tool = make_sql_query(
        {"conv_id": "conv-1"}, _SQLConnector(result=[("value",), (1,)])
    )

    response = json.loads(tool(sql="SELECT 1 AS value"))

    assert response == {
        "chunks": [
            {
                "output_type": "markdown",
                "content": "| value |\n| --- |\n| 1 |",
            }
        ]
    }


def test_sql_tool_uses_turn_state_sink_when_context_binding_is_absent() -> None:
    sink = _sink()
    tool = make_sql_query(
        {"conv_id": "conv-1", "_report_artifact_sink": sink},
        _SQLConnector(result=[["value"], [1]]),
    )

    tool(sql="SELECT 1 AS value")

    artifact = sink.snapshot()[0]
    assert isinstance(artifact, SQLArtifact)
    assert artifact.result is not None
    assert artifact.result.inline == {"columns": ["value"], "rows": [[1]]}


def test_sql_tool_exposes_bounded_real_result_to_later_python_steps() -> None:
    """The Python interpreter receives query output, never database credentials."""
    sink = _sink()
    state = {"conv_id": "conv-1", "_report_artifact_sink": sink}

    make_sql_query(
        state,
        _SQLConnector(result=[["month", "gmv"], ["2018-01", 12.5]]),
    )(sql="SELECT month, gmv FROM monthly_sales")

    assert state["_report_sql_results"] == [
        {
            "artifact_id": sink.snapshot()[0].artifact_id,
            "sql": "SELECT month, gmv FROM monthly_sales",
            "columns": ["month", "gmv"],
            "rows": [["2018-01", 12.5]],
            "row_count": 1,
            "truncated": False,
        }
    ]


def test_sql_tool_without_sink_does_not_inspect_database_identity() -> None:
    class _ConnectorWithDangerousIdentity(_SQLConnector):
        @property
        def _db_name(self):
            raise RuntimeError("identity lookup must remain lazy")

        @_db_name.setter
        def _db_name(self, _value):
            pass

    tool = make_sql_query(
        {"conv_id": "conv-1"},
        _ConnectorWithDangerousIdentity(result=[("value",), (1,)]),
    )

    response = json.loads(tool(sql="SELECT 1 AS value"))

    assert response["chunks"][0]["content"] == "| value |\n| --- |\n| 1 |"


def test_sql_artifact_bounds_preview_but_hashes_every_row() -> None:
    rows = [(index, "x" * 5_000) for index in range(150)]
    connector = _SQLConnector(result=[["id", "payload"], *rows])
    sink = _sink()

    with bind_artifact_sink(sink):
        make_sql_query({"conv_id": "conv-1"}, connector)(sql="SELECT * FROM sales")

    artifact = sink.snapshot()[0]
    assert isinstance(artifact, SQLArtifact)
    assert artifact.row_count == 150
    assert artifact.result is not None
    assert len(artifact.result.inline["rows"]) <= MAX_SQL_PREVIEW_ROWS
    assert artifact.result.inline["truncated"] is True
    assert artifact.result.inline["complete_row_count"] == 150
    assert artifact.result.size < 300_000
    assert artifact.metadata["output_summary"]["complete_result_sha256"] == (
        sha256_json(
            {
                "columns": ["id", "payload"],
                "rows": [[index, "x" * 5_000] for index in range(150)],
            }
        )
    )


def test_subagent_sql_delegates_to_the_shared_capture_adapter() -> None:
    sink = _sink()
    tool = make_react_tools(
        {"conv_id": "conv-1"},
        database_connector=_SQLConnector(result=[["value"], [1]]),
    )["sql_query"]

    with bind_artifact_sink(sink):
        tool(sql="SELECT 1 AS value")

    artifact = sink.snapshot()[0]
    assert isinstance(artifact, SQLArtifact)
    assert artifact.result is not None
    assert artifact.result.inline == {"columns": ["value"], "rows": [[1]]}


def _patch_code_paths(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "dbgpt.configs.model_config.PILOT_PATH", str(tmp_path / "pilot-root")
    )
    monkeypatch.setattr(
        "dbgpt.configs.model_config.STATIC_MESSAGE_IMG_PATH",
        str(tmp_path / "static-images"),
    )


@pytest.mark.asyncio
async def test_python_tool_captures_full_output_hash_before_display_truncation(
    tmp_path, monkeypatch
) -> None:
    stdout = ("result-line\n" * 6_000).encode()

    async def seam(script_path, *, cwd, env, timeout=60):
        return 0, stdout, b"warning"

    monkeypatch.setattr(code_interpreter_module, "_run_python_file", seam)
    _patch_code_paths(monkeypatch, tmp_path)
    sink = _sink()
    tool = code_interpreter_module.make_code_interpreter({"conv_id": "conv-1"})

    with bind_artifact_sink(sink):
        response = json.loads(await tool(code="print('result')"))

    artifact = sink.snapshot()[0]
    assert isinstance(artifact, PythonArtifact)
    assert artifact.status == "succeeded"
    assert artifact.exit_code == 0
    assert artifact.timing is not None
    assert artifact.timing.server_execution_ms == artifact.duration_ms
    assert artifact.stdout == stdout.decode()[:50_000]
    assert artifact.stderr == "warning"
    assert artifact.metadata["output_summary"]["stdout_chars"] == len(stdout)
    assert artifact.metadata["output_summary"]["stdout_sha256"]
    assert "Output truncated" in response["chunks"][1]["content"]


@pytest.mark.asyncio
async def test_python_without_sink_decodes_stdout_once(tmp_path, monkeypatch) -> None:
    class _DecodeOnce(bytes):
        calls = 0

        def decode(self, *args, **kwargs):
            self.calls += 1
            if self.calls > 1:
                raise AssertionError("stdout decoded more than once")
            return super().decode(*args, **kwargs)

    stdout = _DecodeOnce(b"result")

    async def seam(script_path, *, cwd, env, timeout=60):
        return 0, stdout, b""

    monkeypatch.setattr(code_interpreter_module, "_run_python_file", seam)
    _patch_code_paths(monkeypatch, tmp_path)
    tool = code_interpreter_module.make_code_interpreter({"conv_id": "conv-1"})

    response = json.loads(await tool(code="print('result')"))

    assert response["chunks"][1]["content"] == "result"
    assert stdout.calls == 1


@pytest.mark.asyncio
async def test_python_tool_receives_prior_sql_results_through_read_only_env(
    tmp_path, monkeypatch
) -> None:
    """SQL results are injected as JSON and exposed by sql_result_frame()."""
    calls = []

    async def seam(script_path, *, cwd, env, timeout=60):
        calls.append(
            {
                "source": Path(script_path).read_text(encoding="utf-8"),
                "context": json.loads(
                    Path(env["SQL_RESULTS_PATH"]).read_text(encoding="utf-8")
                ),
                "context_path": env["SQL_RESULTS_PATH"],
            }
        )
        return 0, b"sql-context-ok", b""

    monkeypatch.setattr(code_interpreter_module, "_run_python_file", seam)
    _patch_code_paths(monkeypatch, tmp_path)
    state = {
        "conv_id": "sql-context",
        "_report_sql_results": [
            {
                "artifact_id": "sql-monthly",
                "sql": "SELECT month, gmv FROM monthly_sales",
                "columns": ["month", "gmv"],
                "rows": [["2018-01", 12.5]],
                "row_count": 1,
                "truncated": False,
            }
        ],
    }

    response = json.loads(
        await code_interpreter_module.make_code_interpreter(state)(
            code="frame = sql_result_frame(-1)\nprint(frame.iloc[0]['gmv'])"
        )
    )

    assert calls[0]["context"] == state["_report_sql_results"]
    assert "def sql_result_frame(index=-1):" in calls[0]["source"]
    assert "SQL_RESULTS_PATH" in calls[0]["source"]
    assert not Path(calls[0]["context_path"]).exists()
    assert response["chunks"][1]["content"] == "sql-context-ok"


@pytest.mark.asyncio
async def test_python_sql_context_usage_records_sql_lineage(tmp_path, monkeypatch) -> None:
    async def seam(script_path, *, cwd, env, timeout=60):
        return 0, b"ok", b""

    monkeypatch.setattr(code_interpreter_module, "_run_python_file", seam)
    _patch_code_paths(monkeypatch, tmp_path)
    sink = _sink()
    state = {
        "conv_id": "sql-lineage",
        "_report_artifact_sink": sink,
    }
    make_sql_query(
        state,
        _SQLConnector(result=[["month", "gmv"], ["2018-01", 12.5]]),
    )(sql="SELECT month, gmv FROM monthly_sales")
    sql_artifact = sink.snapshot()[0]

    await code_interpreter_module.make_code_interpreter(state)(
        code="frame = sql_result_frame(-1)\nprint(len(frame))"
    )

    artifact = sink.snapshot()[1]
    assert isinstance(artifact, PythonArtifact)
    assert artifact.input_artifact_ids == [sql_artifact.artifact_id]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("returncode", "stderr", "expected_status", "expected_error"),
    [
        (2, b"boom", "failed", "boom"),
        (None, b"", "timed_out", "Execution timed out (60s limit)"),
    ],
)
async def test_python_tool_captures_failure_and_timeout_process_state(
    tmp_path,
    monkeypatch,
    returncode,
    stderr: bytes,
    expected_status: str,
    expected_error: str,
) -> None:
    async def seam(script_path, *, cwd, env, timeout=60):
        return returncode, b"partial", stderr

    monkeypatch.setattr(code_interpreter_module, "_run_python_file", seam)
    _patch_code_paths(monkeypatch, tmp_path)
    sink = _sink()
    tool = code_interpreter_module.make_code_interpreter({"conv_id": "conv-1"})

    with bind_artifact_sink(sink):
        await tool(code="raise RuntimeError('boom')")

    artifact = sink.snapshot()[0]
    assert isinstance(artifact, PythonArtifact)
    assert artifact.status == expected_status
    assert artifact.exit_code == returncode
    assert artifact.error == expected_error


@pytest.mark.asyncio
async def test_python_generated_image_creates_dependent_chart_artifact(
    tmp_path, monkeypatch
) -> None:
    image_bytes = b"x" * (3 * 1024 * 1024)

    async def seam(script_path, *, cwd, env, timeout=60):
        Path(cwd, "plot.png").write_bytes(image_bytes)
        return 0, b"saved plot", b""

    monkeypatch.setattr(code_interpreter_module, "_run_python_file", seam)
    _patch_code_paths(monkeypatch, tmp_path)
    sink = _sink()
    state = {"conv_id": "conv-1"}
    tool = code_interpreter_module.make_code_interpreter(state)

    with bind_artifact_sink(sink):
        await tool(code="save_plot()")

    python, chart = sink.snapshot()
    assert isinstance(python, PythonArtifact)
    assert isinstance(chart, ChartArtifact)
    assert chart.input_artifact_ids == [python.artifact_id]
    assert chart.output.uri == state["generated_images"][0]
    assert chart.output.inline is None
    assert chart.output.sha256 is not None
    assert chart.output.size == len(image_bytes)


def test_capture_errors_are_bounded_with_complete_hash() -> None:
    error = "database unavailable " * 1_000
    sink = _sink()

    with bind_artifact_sink(sink):
        make_sql_query(
            {"conv_id": "conv-1"},
            _SQLConnector(error=RuntimeError(error)),
        )(sql="SELECT id FROM sales")

    artifact = sink.snapshot()[0]
    assert isinstance(artifact, SQLArtifact)
    assert artifact.error is not None
    assert len(artifact.error) == 8_000
    summary = artifact.metadata["output_summary"]
    assert summary["error_chars"] == len(error)
    assert summary["error_truncated"] is True
    assert summary["error_sha256"]


@pytest.mark.asyncio
async def test_subagent_preserves_legacy_output_limits_without_sink(
    tmp_path, monkeypatch
) -> None:
    stdout = b"x" * 3_000

    async def seam(script_path, *, cwd, env, timeout=60):
        return 0, stdout, b""

    monkeypatch.setattr(code_interpreter_module, "_run_python_file", seam)
    _patch_code_paths(monkeypatch, tmp_path)
    tools = make_react_tools(
        {"conv_id": "conv-1"},
        database_connector=_SQLConnector(
            result=[["payload"], *[["x" * 1_000] for _ in range(60)]]
        ),
    )

    sql_response = json.loads(tools["sql_query"](sql="SELECT payload FROM sales"))
    python_response = json.loads(
        await tools["code_interpreter"](code="print('x' * 3000)")
    )

    assert "Output truncated at 20000 chars" not in sql_response["chunks"][0]["content"]
    python_text = python_response["chunks"][1]["content"]
    assert python_text.startswith("x" * 2_000)
    assert "Only showing first 2000 chars" in python_text
    assert "If you generated HTML, the file is saved." in python_text


@pytest.mark.asyncio
async def test_subagent_without_sink_preserves_legacy_globals_and_syntax_text(
    tmp_path, monkeypatch
) -> None:
    _patch_code_paths(monkeypatch, tmp_path)
    python_tool = make_react_tools({"conv_id": "conv-1"}, database_connector=None)[
        "code_interpreter"
    ]

    globals_response = json.loads(
        await python_tool(
            code='print(FILE_PATH if "FILE_PATH" in globals() else "missing")'
        )
    )
    invalid_code = "if True print('broken')\n" + "print('tail')\n" * 11
    syntax_response = json.loads(await python_tool(code=invalid_code))

    assert globals_response["chunks"][1]["content"] == "missing"
    syntax_text = syntax_response["chunks"][1]["content"]
    assert "SyntaxError before execution" in syntax_text
    assert "split long tasks into multiple code_interpreter calls" in syntax_text


@pytest.mark.asyncio
async def test_subagent_with_sink_uses_shared_python_capture(
    tmp_path, monkeypatch
) -> None:
    async def seam(script_path, *, cwd, env, timeout=60):
        return 0, b"captured", b""

    monkeypatch.setattr(code_interpreter_module, "_run_python_file", seam)
    _patch_code_paths(monkeypatch, tmp_path)
    sink = _sink()
    python_tool = make_react_tools({"conv_id": "conv-1"}, database_connector=None)[
        "code_interpreter"
    ]

    with bind_artifact_sink(sink):
        response = json.loads(await python_tool(code="print('captured')"))

    assert response["chunks"][1]["content"] == "captured"
    artifact = sink.snapshot()[0]
    assert isinstance(artifact, PythonArtifact)
    assert artifact.stdout == "captured"
