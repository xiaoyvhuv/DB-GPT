"""Tests for ReAct agent stream terminal-event lifecycle handling."""

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from dbgpt.core.interface.report import (
    ArtifactContent,
    ExecutionReference,
    SQLArtifact,
)
from dbgpt.core.interface.report_capture import (
    ArtifactCaptureContext,
    InMemoryArtifactSink,
    get_current_artifact_sink,
)
from dbgpt_app.openapi.api_v1 import agentic_data_api
from dbgpt_app.openapi.api_v1.react_final import AgentCitation, AgentFinalAnswer
from dbgpt_serve.report.serve import ReportServe


def _decode_sse_event(event: str):
    assert event.startswith("data: ")
    return json.loads(event.removeprefix("data: ").strip())


def test_extracts_explicit_recommendation_count_from_user_request() -> None:
    assert (
        agentic_data_api._requested_recommendation_count(
            "分析经营表现，最后给出三条可执行的经营建议。"
        )
        == 3
    )
    assert agentic_data_api._requested_recommendation_count("概览 GMV 趋势") is None


def test_history_failure_does_not_drop_final_or_done(caplog) -> None:
    class _FailingStorageConversation:
        def add_view_message(self, payload: str) -> None:
            raise RuntimeError("history storage unavailable")

        def end_current_round(self) -> None:
            raise AssertionError("must stop after the first persistence failure")

        def save_to_storage(self) -> None:
            raise AssertionError("must stop after the first persistence failure")

    events = agentic_data_api._react_terminal_events(
        _FailingStorageConversation(),
        '{"type":"react-agent"}',
        AgentFinalAnswer(content="answer"),
    )

    assert [_decode_sse_event(event) for event in events] == [
        {
            "type": "final",
            "protocol_version": 2,
            "content": "answer",
            "citations": [],
        },
        {"type": "done"},
    ]
    assert "Failed to persist ReAct agent history" in caplog.text


def test_report_link_is_persisted_and_emitted_with_terminal_answer() -> None:
    storage = MagicMock()
    answer = AgentFinalAnswer(
        content="answer", report_id="report-123", report_run_id="turn-123"
    )
    history = agentic_data_api._build_react_history_payload(
        final_content=answer.content,
        steps=[],
        task_plan=[],
        generated_images=[],
        sub_agents={},
        input_files=[],
        report_id=answer.report_id,
        report_run_id=answer.report_run_id,
    )

    events = agentic_data_api._react_terminal_events(storage, history, answer)

    assert json.loads(history)["report_id"] == "report-123"
    assert json.loads(history)["report_run_id"] == "turn-123"
    assert _decode_sse_event(events[0])["report_id"] == "report-123"
    assert _decode_sse_event(events[0])["report_run_id"] == "turn-123"


@pytest.mark.asyncio
async def test_agent_operation_binds_and_restores_turn_artifact_sink() -> None:
    sink = InMemoryArtifactSink(
        ArtifactCaptureContext(conv_uid="conv-1", run_id="turn-1")
    )

    async def operation() -> str:
        assert get_current_artifact_sink() is sink
        await asyncio.sleep(0)
        assert get_current_artifact_sink() is sink
        return "done"

    assert get_current_artifact_sink() is None
    result = await agentic_data_api._run_with_artifact_capture(sink, operation)

    assert result == "done"
    assert get_current_artifact_sink() is None


def test_captured_artifact_is_correlated_with_visible_execution_step() -> None:
    sink = InMemoryArtifactSink(
        ArtifactCaptureContext(conv_uid="conv-1", run_id="turn-1")
    )
    sink.record(
        SQLArtifact(
            artifact_id="sql-1",
            execution=ExecutionReference(
                conv_uid="conv-1",
                step_id="captured-step",
                run_id="turn-1",
                action="sql_query",
            ),
            created_at=datetime.now(timezone.utc),
            status="succeeded",
            duration_ms=1,
            sql="SELECT 1",
            columns=["value"],
            row_count=1,
            result=ArtifactContent(
                inline={"columns": ["value"], "rows": [[1]]}
            ),
        )
    )
    state = {}

    agentic_data_api._record_visible_artifact_step_ids(
        sink,
        state,
        action="sql_query",
        visible_step_id="visible-step-7",
    )

    assert state["_report_execution_step_map"] == {
        "captured-step": "visible-step-7"
    }


def test_subagent_sql_step_is_captured_as_tabular_artifact() -> None:
    """Parallel sub-agent steps must reach the same report sink as lead tools."""
    sink = InMemoryArtifactSink(
        ArtifactCaptureContext(conv_uid="conv-1", run_id="turn-1")
    )

    agentic_data_api._capture_subagent_step_artifact(
        sink,
        {
            "type": "agent.step",
            "agent_id": "sub_d1_0",
            "batch_id": 1,
            "round": 2,
            "action": "sql_query",
            "intention": "Count orders by month",
            "sql": "SELECT month, COUNT(*) AS orders FROM orders GROUP BY month",
            "chunks": [
                {
                    "output_type": "markdown",
                    "content": "| month | orders |\n| --- | --- |\n| 2024-01 | 42 |",
                }
            ],
        },
    )

    artifacts = sink.snapshot()
    assert len(artifacts) == 1
    artifact = artifacts[0]
    assert isinstance(artifact, SQLArtifact)
    assert artifact.execution.step_id == "sub_d1_0:2"
    assert artifact.execution.action == "sql_query"
    assert artifact.sql == "SELECT month, COUNT(*) AS orders FROM orders GROUP BY month"
    assert artifact.columns == ["month", "orders"]
    assert artifact.row_count == 1
    assert artifact.result.inline == {
        "columns": ["month", "orders"],
        "rows": [["2024-01", 42]],
    }


@pytest.mark.asyncio
async def test_completed_turn_assembles_and_persists_captured_report(
    monkeypatch,
) -> None:
    sink = InMemoryArtifactSink(
        ArtifactCaptureContext(conv_uid="conv-1", run_id="turn-1")
    )
    sink.record(
        SQLArtifact(
            artifact_id="sql-total",
            execution=ExecutionReference(
                conv_uid="conv-1", step_id="step-1", run_id="turn-1"
            ),
            created_at=datetime(2026, 8, 24, tzinfo=timezone.utc),
            status="succeeded",
            duration_ms=2,
            sql="SELECT 42 AS total",
            columns=["total"],
            row_count=1,
            result=ArtifactContent(inline={"columns": ["total"], "rows": [[42]]}),
        )
    )
    service = MagicMock()
    expected = SimpleNamespace(run_id="run-1", report_id="report-1")
    service.assemble_and_save.return_value = expected
    monkeypatch.setattr(
        ReportServe,
        "get_current_serve",
        classmethod(lambda _cls, _app: SimpleNamespace(service=service)),
    )
    final_answer = AgentFinalAnswer(
        content="The total is 42.",
        citations=(
            AgentCitation(
                index=1,
                id="chunk-1",
                source_name="Sales handbook",
                excerpt="Definition of total sales.",
            ),
        ),
    )

    actual = await agentic_data_api._assemble_and_persist_turn_report(
        dialogue=SimpleNamespace(user_name="alice"),
        conv_id="conv-1",
        user_input="Analyze total sales\nwith evidence",
        final_answer=final_answer,
        artifact_sink=sink,
        presentation={"format": "html", "content": "<html></html>"},
        execution_step_map={"step-1": "visible-step-7"},
    )

    assert actual is expected
    kwargs = service.assemble_and_save.call_args.kwargs
    assert kwargs["owner_id"] == "alice"
    assert kwargs["report_id"] == ("report-36524fd8f6747fc2712506d01fee0e18b48cd626")
    assert kwargs["title"] == "Analyze total sales"
    assert kwargs["conv_uid"] == "conv-1"
    assert kwargs["run_id"] == "turn-1"
    assert kwargs["artifacts"][0].artifact_id == "sql-total"
    assert kwargs["artifacts"][0].execution.step_id == "visible-step-7"
    assert "supporting_artifact_ids" not in kwargs
    assert kwargs["metadata"] == {
        "source": "react_agent",
        "capture_run_id": "turn-1",
        "presentation_status": "rendered",
    }


@pytest.mark.asyncio
async def test_unrendered_turn_does_not_persist_as_completed_report(monkeypatch) -> None:
    sink = InMemoryArtifactSink(
        ArtifactCaptureContext(conv_uid="conv-1", run_id="turn-1")
    )
    sink.record(
        SQLArtifact(
            artifact_id="sql-total",
            execution=ExecutionReference(
                conv_uid="conv-1", step_id="step-1", run_id="turn-1"
            ),
            created_at=datetime(2026, 8, 24, tzinfo=timezone.utc),
            status="succeeded",
            duration_ms=2,
            sql="SELECT 42 AS total",
            columns=["total"],
            row_count=1,
            result=ArtifactContent(inline={"columns": ["total"], "rows": [[42]]}),
        )
    )
    service = MagicMock()
    monkeypatch.setattr(
        ReportServe,
        "get_current_serve",
        classmethod(lambda _cls, _app: SimpleNamespace(service=service)),
    )

    actual = await agentic_data_api._assemble_and_persist_turn_report(
        dialogue=SimpleNamespace(user_name="alice"),
        conv_id="conv-1",
        user_input="Analyze total sales",
        final_answer=AgentFinalAnswer(content="The total is 42."),
        artifact_sink=sink,
    )

    assert actual is None
    service.assemble_and_save.assert_not_called()


@pytest.mark.asyncio
async def test_report_persistence_failure_does_not_break_completed_turn(
    monkeypatch, caplog
) -> None:
    sink = InMemoryArtifactSink(
        ArtifactCaptureContext(conv_uid="conv-1", run_id="turn-1")
    )
    sink.record(
        SQLArtifact(
            artifact_id="sql-failed",
            execution=ExecutionReference(
                conv_uid="conv-1", step_id="step-1", run_id="turn-1"
            ),
            created_at=datetime(2026, 8, 24, tzinfo=timezone.utc),
            status="failed",
            duration_ms=2,
            error="database unavailable",
            sql="SELECT 1",
        )
    )
    service = MagicMock()
    service.assemble_and_save.side_effect = RuntimeError("storage unavailable")
    monkeypatch.setattr(
        ReportServe,
        "get_current_serve",
        classmethod(lambda _cls, _app: SimpleNamespace(service=service)),
    )

    actual = await agentic_data_api._assemble_and_persist_turn_report(
        dialogue=SimpleNamespace(user_name="alice"),
        conv_id="conv-1",
        user_input="Run query",
        final_answer=AgentFinalAnswer(content="The query failed."),
        artifact_sink=sink,
        presentation={"format": "html", "content": "<html></html>"},
    )

    assert actual is None
    assert "Failed to assemble or persist report" in caplog.text


@pytest.mark.asyncio
async def test_report_persistence_scheduler_is_background_and_bounded(
    monkeypatch, caplog
) -> None:
    never = asyncio.Event()

    async def _never_persist(**_kwargs):
        await never.wait()

    monkeypatch.setattr(
        agentic_data_api,
        "_assemble_and_persist_turn_report",
        _never_persist,
    )
    monkeypatch.setattr(agentic_data_api, "_REPORT_PERSIST_TIMEOUT_SECONDS", 0.1)

    task = agentic_data_api._schedule_turn_report_persistence(
        dialogue=SimpleNamespace(user_name="alice"),
        conv_id="conv-1",
        user_input="Analyze sales",
        final_answer=AgentFinalAnswer(content="Answer"),
        artifact_sink=None,
        react_state={},
    )

    assert not task.done()
    await task
    assert "Report persistence timed out" in caplog.text


@pytest.mark.asyncio
async def test_report_timeout_abandons_blocking_service_wait(
    monkeypatch, caplog
) -> None:
    started = threading.Event()
    finished = threading.Event()
    release = threading.Event()

    def _block_save(**_kwargs):
        started.set()
        try:
            release.wait(timeout=1)
        finally:
            finished.set()

    service = MagicMock()
    service.assemble_and_save.side_effect = _block_save
    monkeypatch.setattr(
        ReportServe,
        "get_current_serve",
        classmethod(lambda _cls, _app: SimpleNamespace(service=service)),
    )
    monkeypatch.setattr(agentic_data_api, "_REPORT_PERSIST_TIMEOUT_SECONDS", 0.1)
    sink = InMemoryArtifactSink(
        ArtifactCaptureContext(conv_uid="conv-1", run_id="turn-1")
    )
    sink.record(
        SQLArtifact(
            artifact_id="sql-blocking",
            execution=ExecutionReference(
                conv_uid="conv-1", step_id="step-1", run_id="turn-1"
            ),
            created_at=datetime(2026, 8, 24, tzinfo=timezone.utc),
            status="failed",
            duration_ms=2,
            error="database unavailable",
            sql="SELECT 1",
        )
    )

    task = agentic_data_api._schedule_turn_report_persistence(
        dialogue=SimpleNamespace(user_name="alice"),
        conv_id="conv-1",
        user_input="Analyze sales",
        final_answer=AgentFinalAnswer(content="Answer"),
        artifact_sink=sink,
        react_state={"_report_presentation": {"format": "html", "content": "<html></html>"}},
    )
    try:
        assert await asyncio.to_thread(started.wait, 1)
        await asyncio.wait_for(task, timeout=0.5)
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 1)

    assert "Report persistence timed out" in caplog.text


@pytest.mark.asyncio
async def test_report_persistence_capacity_does_not_exhaust_shared_executor(
    monkeypatch, caplog
) -> None:
    executor = ThreadPoolExecutor(
        max_workers=2, thread_name_prefix="test-report-persist"
    )
    slots = threading.BoundedSemaphore(2)
    started = threading.Semaphore(0)
    release = threading.Event()

    def _block_save(**_kwargs):
        started.release()
        release.wait(timeout=1)

    service = MagicMock()
    service.assemble_and_save.side_effect = _block_save
    monkeypatch.setattr(
        ReportServe,
        "get_current_serve",
        classmethod(lambda _cls, _app: SimpleNamespace(service=service)),
    )
    monkeypatch.setattr(agentic_data_api, "_REPORT_PERSIST_EXECUTOR", executor)
    monkeypatch.setattr(agentic_data_api, "_REPORT_PERSIST_SLOTS", slots)
    monkeypatch.setattr(agentic_data_api, "_REPORT_PERSIST_TIMEOUT_SECONDS", 0.1)
    sink = InMemoryArtifactSink(
        ArtifactCaptureContext(conv_uid="conv-1", run_id="turn-1")
    )
    sink.record(
        SQLArtifact(
            artifact_id="sql-capacity",
            execution=ExecutionReference(
                conv_uid="conv-1", step_id="step-1", run_id="turn-1"
            ),
            created_at=datetime(2026, 8, 24, tzinfo=timezone.utc),
            status="failed",
            duration_ms=2,
            error="database unavailable",
            sql="SELECT 1",
        )
    )

    def schedule():
        return agentic_data_api._schedule_turn_report_persistence(
            dialogue=SimpleNamespace(user_name="alice"),
            conv_id="conv-1",
            user_input="Analyze sales",
                final_answer=AgentFinalAnswer(content="Answer"),
                artifact_sink=sink,
                react_state={"_report_presentation": {"format": "html", "content": "<html></html>"}},
        )

    first = schedule()
    second = schedule()
    try:
        assert await asyncio.to_thread(started.acquire, True, 1)
        assert await asyncio.to_thread(started.acquire, True, 1)
        await asyncio.gather(first, second)

        third = schedule()
        await third

        assert "capacity is saturated" in caplog.text
        assert service.assemble_and_save.call_count == 2
        assert await asyncio.to_thread(lambda: "shared executor available") == (
            "shared executor available"
        )
    finally:
        release.set()
        executor.shutdown(wait=True, cancel_futures=True)


@pytest.mark.asyncio
async def test_report_persistence_skips_empty_snapshot_and_owner(monkeypatch) -> None:
    service = MagicMock()
    monkeypatch.setattr(
        ReportServe,
        "get_current_serve",
        classmethod(lambda _cls, _app: SimpleNamespace(service=service)),
    )
    empty_sink = InMemoryArtifactSink(
        ArtifactCaptureContext(conv_uid="conv-1", run_id="turn-1")
    )

    assert (
        await agentic_data_api._assemble_and_persist_turn_report(
            dialogue=SimpleNamespace(user_name="alice"),
            conv_id="conv-1",
            user_input="Analyze sales",
            final_answer=AgentFinalAnswer(content="Answer"),
            artifact_sink=empty_sink,
        )
        is None
    )
    assert (
        await agentic_data_api._assemble_and_persist_turn_report(
            dialogue=SimpleNamespace(user_name=""),
            conv_id="conv-1",
            user_input="Analyze sales",
            final_answer=AgentFinalAnswer(content="Answer"),
            artifact_sink=empty_sink,
        )
        is None
    )
    service.assemble_and_save.assert_not_called()


@pytest.mark.asyncio
async def test_closing_stream_cancels_and_awaits_agent_task(monkeypatch) -> None:
    task_started = asyncio.Event()
    task_finished = asyncio.Event()
    created_tasks = []

    async def _agent_work() -> None:
        task_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            task_finished.set()

    async def _fake_stream_impl(
        dialogue, tool_mode="full", attachment_ctx=None, agent_task_holder=None
    ):
        del dialogue, tool_mode, attachment_ctx
        task = asyncio.create_task(_agent_work())
        created_tasks.append(task)
        agent_task_holder.append(task)
        await task_started.wait()
        yield "data: first\n\n"
        await asyncio.Event().wait()

    monkeypatch.setattr(
        agentic_data_api,
        "_react_agent_stream_impl",
        _fake_stream_impl,
    )

    stream = agentic_data_api._react_agent_stream(SimpleNamespace())
    assert await anext(stream) == "data: first\n\n"

    await stream.aclose()

    assert len(created_tasks) == 1
    assert created_tasks[0].cancelled()
    assert created_tasks[0].done()
    assert task_finished.is_set()


@pytest.mark.asyncio
async def test_runtime_failure_emits_structured_final_and_done(
    monkeypatch, caplog
) -> None:
    async def _failing_stream_impl(
        dialogue, tool_mode="full", attachment_ctx=None, agent_task_holder=None
    ):
        del dialogue, tool_mode, attachment_ctx, agent_task_holder
        if False:
            yield ""
        raise RuntimeError("model stream failed")

    monkeypatch.setattr(
        agentic_data_api,
        "_react_agent_stream_impl",
        _failing_stream_impl,
    )

    events = [
        _decode_sse_event(event)
        async for event in agentic_data_api._react_agent_stream(SimpleNamespace())
    ]

    assert events == [
        {
            "type": "final",
            "protocol_version": 2,
            "content": "抱歉，回答生成过程中发生错误，请重试。",
            "citations": [],
        },
        {"type": "done"},
    ]
    assert "ReAct agent stream failed before normal completion" in caplog.text


@pytest.mark.asyncio
async def test_runtime_failure_does_not_duplicate_a_final_event(monkeypatch) -> None:
    async def _partially_failing_stream_impl(
        dialogue, tool_mode="full", attachment_ctx=None, agent_task_holder=None
    ):
        del dialogue, tool_mode, attachment_ctx, agent_task_holder
        yield agentic_data_api._sse_event(
            AgentFinalAnswer(content="answer").to_sse_payload()
        )
        raise RuntimeError("failed after final")

    monkeypatch.setattr(
        agentic_data_api,
        "_react_agent_stream_impl",
        _partially_failing_stream_impl,
    )

    events = [
        _decode_sse_event(event)
        async for event in agentic_data_api._react_agent_stream(SimpleNamespace())
    ]

    assert [event["type"] for event in events] == ["final", "done"]
    assert events[0]["content"] == "answer"


@pytest.mark.asyncio
async def test_response_disconnect_closes_stream_and_agent_task(monkeypatch) -> None:
    task_started = asyncio.Event()
    task_finished = asyncio.Event()
    body_send_started = asyncio.Event()
    never = asyncio.Event()
    created_tasks = []

    async def _agent_work() -> None:
        task_started.set()
        try:
            await never.wait()
        finally:
            task_finished.set()

    async def _fake_stream_impl(
        dialogue, tool_mode="full", attachment_ctx=None, agent_task_holder=None
    ):
        del dialogue, tool_mode, attachment_ctx
        task = asyncio.create_task(_agent_work())
        created_tasks.append(task)
        agent_task_holder.append(task)
        await task_started.wait()
        yield agentic_data_api._sse_event({"type": "step", "content": "first"})
        await never.wait()

    async def _send(message) -> None:
        if message["type"] == "http.response.body" and message.get("more_body"):
            body_send_started.set()
            await never.wait()

    async def _receive():
        await body_send_started.wait()
        return {"type": "http.disconnect"}

    monkeypatch.setattr(
        agentic_data_api,
        "_react_agent_stream_impl",
        _fake_stream_impl,
    )
    response = agentic_data_api._AgentStreamingResponse(
        agentic_data_api._react_agent_stream(SimpleNamespace()),
        media_type="text/event-stream",
    )
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/chat/react-agent",
        "headers": [],
        "asgi": {"version": "3.0", "spec_version": "2.0"},
    }

    await asyncio.wait_for(response(scope, _receive, _send), timeout=1)

    assert len(created_tasks) == 1
    assert created_tasks[0].cancelled()
    assert created_tasks[0].done()
    assert task_finished.is_set()
