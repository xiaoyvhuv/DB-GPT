"""Tests for report evidence emitted by native agent memory reads."""

from types import SimpleNamespace

import pytest

from dbgpt.agent.core.memory.agent_memory import AgentMemoryFragment
from dbgpt.agent.core.role import Role
from dbgpt.core.interface.report import ReferenceArtifact
from dbgpt.core.interface.report_capture import (
    ArtifactCaptureContext,
    InMemoryArtifactSink,
    bind_artifact_sink,
    sha256_text,
)


class _Memory:
    def __init__(self, fragments):
        self.fragments = fragments

    async def read(self, question):
        assert question == "What happened?"
        return self.fragments


@pytest.mark.asyncio
async def test_read_memories_captures_id_and_content_version() -> None:
    fragment = AgentMemoryFragment(
        "Thought: hidden chain of thought. Action Input: api_key=secret. "
        "Observation: GMV 42.",
        memory_id=123,
        importance=0.8,
    )
    fake_role = SimpleNamespace(memory=_Memory([fragment]))
    sink = InMemoryArtifactSink(
        ArtifactCaptureContext(conv_uid="conv-1", run_id="run-1")
    )

    with bind_artifact_sink(sink):
        result = await Role.read_memories(fake_role, "What happened?")

    assert result == fragment.raw_observation
    artifact = sink.snapshot()[0]
    assert isinstance(artifact, ReferenceArtifact)
    assert artifact.reference_type == "memory"
    assert artifact.source_id == "123"
    version = sha256_text(fragment.raw_observation)
    assert artifact.uri is None
    assert artifact.excerpt is None
    assert artifact.locator == {
        "memory_id": 123,
        "version": version,
        "result_index": 0,
    }
    assert artifact.metadata["content_persisted"] is False
    serialized = artifact.model_dump_json()
    assert "hidden chain of thought" not in serialized
    assert "api_key" not in serialized
    assert "secret" not in serialized


@pytest.mark.asyncio
async def test_read_memories_without_sink_does_not_inspect_capture_fields() -> None:
    class _MinimalFragment:
        raw_observation = "legacy memory"

        @property
        def id(self):
            raise AssertionError("memory id is capture-only")

    fake_role = SimpleNamespace(memory=_Memory([_MinimalFragment()]))

    result = await Role.read_memories(fake_role, "What happened?")

    assert result == "legacy memory"
