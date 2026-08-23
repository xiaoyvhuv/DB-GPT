"""Tests for file-oriented knowledge evidence capture."""

import json
from types import SimpleNamespace

import pytest

from dbgpt.core.interface.report import ReferenceArtifact
from dbgpt.core.interface.report_capture import (
    ArtifactCaptureContext,
    InMemoryArtifactSink,
    bind_artifact_sink,
)
from dbgpt_serve.rag.tools import kb_file_tools as kb_module


class _ChunkEntity:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _ChunkDao:
    def __init__(self, chunks):
        self.chunks = chunks

    def get_document_chunks(self, entity, page, page_size):
        return self.chunks


def _sink():
    return InMemoryArtifactSink(
        ArtifactCaptureContext(conv_uid="conv-1", run_id="run-1")
    )


@pytest.mark.asyncio
async def test_kb_grep_captures_matching_native_chunk(monkeypatch) -> None:
    chunk = SimpleNamespace(
        id=91,
        content="first line\nRevenue grew by 20%.",
        meta_info=json.dumps({"chunk_index": 4, "start_line": 20, "page_number": 7}),
    )
    monkeypatch.setattr(
        kb_module,
        "_get_all_file_paths",
        lambda _knowledge_id: [
            {
                "doc_id": 3,
                "doc_name": "annual-report.pdf",
                "file_path": "reports/annual-report.pdf",
                "file_type": "pdf",
                "language": "",
            }
        ],
    )
    monkeypatch.setattr(
        kb_module,
        "_get_chunk_dao",
        lambda: (_ChunkDao([chunk]), _ChunkEntity),
    )
    sink = _sink()

    with bind_artifact_sink(sink):
        result = await kb_module.kb_grep(knowledge_id="finance", query="revenue")

    assert "Revenue grew" in result
    artifact = sink.snapshot()[0]
    assert isinstance(artifact, ReferenceArtifact)
    assert artifact.source_id == "91"
    assert artifact.locator["document_id"] == 3
    assert artifact.locator["chunk_id"] == "91"
    assert artifact.locator["page"] == 7
    assert artifact.locator["matched_lines"] == [21]


@pytest.mark.asyncio
async def test_kb_cat_captures_document_line_range(monkeypatch) -> None:
    chunk = SimpleNamespace(
        content="line one\nline two\nline three",
        meta_info=json.dumps({"chunk_index": 0}),
    )
    monkeypatch.setattr(
        kb_module,
        "_find_doc_by_file_path",
        lambda _knowledge_id, _path: {
            "doc_id": 5,
            "doc_name": "guide.md",
            "file_path": "docs/guide.md",
            "file_type": "markdown",
            "language": "",
        },
    )
    monkeypatch.setattr(
        kb_module,
        "_get_chunk_dao",
        lambda: (_ChunkDao([chunk]), _ChunkEntity),
    )
    sink = _sink()

    with bind_artifact_sink(sink):
        result = await kb_module.kb_cat(
            knowledge_id="docs", path="docs/guide.md", start_line=2, end_line=3
        )

    assert "line two" in result
    artifact = sink.snapshot()[0]
    assert isinstance(artifact, ReferenceArtifact)
    assert artifact.source_id == "document-5-2-3"
    assert artifact.locator == {
        "knowledge_id": "docs",
        "document_id": 5,
        "path": "docs/guide.md",
        "start_line": 2,
        "end_line": 3,
    }
    assert artifact.excerpt == "line two\nline three"
