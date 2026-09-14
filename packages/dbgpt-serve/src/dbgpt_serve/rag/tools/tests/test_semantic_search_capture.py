"""Tests for native knowledge chunk evidence capture."""

from types import SimpleNamespace

import pytest

from dbgpt.core.interface.report import ReferenceArtifact
from dbgpt.core.interface.report_capture import (
    ArtifactCaptureContext,
    InMemoryArtifactSink,
    bind_artifact_sink,
    sha256_text,
)
from dbgpt_serve.rag.tools import semantic_search_tool as semantic_module


class _Service:
    def get(self, query):
        assert query == {"name": "finance"}
        return SimpleNamespace(id=7)

    async def retrieve(self, request, space):
        assert request.space_id == space.id == 7
        return [
            SimpleNamespace(
                chunk_id="chunk-9",
                content="Revenue increased by 20%.",
                score=0.93,
                metadata={
                    "prop_field": {
                        "document_id": 3,
                        "doc_name": "annual-report.pdf",
                        "chunk_index": 8,
                        "page_number": 12,
                        "file_path": "reports/annual-report.pdf",
                    }
                },
            )
        ]


@pytest.mark.asyncio
async def test_semantic_search_captures_document_chunk_and_page(monkeypatch) -> None:
    monkeypatch.setattr(semantic_module, "_get_rag_service", lambda: _Service())
    sink = InMemoryArtifactSink(
        ArtifactCaptureContext(conv_uid="conv-1", run_id="run-1")
    )

    with bind_artifact_sink(sink):
        result = await semantic_module.kb_semantic_search(
            knowledge_id="finance", query="revenue", top_k=5
        )

    assert "Revenue increased" in result
    artifact = sink.snapshot()[0]
    assert isinstance(artifact, ReferenceArtifact)
    assert artifact.source_id == "chunk-9"
    assert artifact.source_name == "annual-report.pdf"
    assert artifact.locator == {
        "knowledge_id": "finance",
        "space_id": 7,
        "document_id": 3,
        "chunk_id": "chunk-9",
        "chunk_index": 8,
        "page": 12,
        "path": "reports/annual-report.pdf",
    }
    assert artifact.metadata["query_sha256"] == sha256_text("revenue")
