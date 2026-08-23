"""Tests for citation metadata emitted by the knowledge retrieval tool."""

import json
from types import SimpleNamespace

import pytest

from dbgpt.core.interface.report import ReferenceArtifact
from dbgpt.core.interface.report_capture import (
    ArtifactCaptureContext,
    InMemoryArtifactSink,
    bind_artifact_sink,
    sha256_text,
)
from dbgpt_app.openapi.api_v1.tools.knowledge_retrieve import (
    make_knowledge_retrieve,
)


class _KnowledgeResource:
    async def retrieve(self, query: str):
        assert query == "revenue"
        return [
            SimpleNamespace(
                chunk_id="chunk-1",
                chunk_name="Revenue section",
                content="Revenue grew by 20 percent.",
                score=0.91,
                metadata={
                    "prop_field": {
                        "document_id": 3,
                        "doc_name": "annual-report.md",
                        "file_path": "reports/annual-report.md",
                        "chunk_index": 8,
                        "page_number": 12,
                        "url": "https://example.test/annual-report",
                    }
                },
            )
        ]


@pytest.mark.asyncio
async def test_knowledge_retrieve_without_sink_preserves_legacy_citation() -> None:
    retrieve = make_knowledge_retrieve({}, [_KnowledgeResource()])

    result = json.loads(await retrieve("revenue"))

    assert result["chunks"] == [
        {
            "output_type": "text",
            "content": "Retrieved 1 relevant documents",
        },
        {
            "output_type": "markdown",
            "content": "[1] Revenue grew by 20 percent.",
        },
    ]
    assert result["citations"] == [
        {
            "id": "chunk-1",
            "sourceName": "Revenue section",
            "chunkIndex": 1,
            "score": 0.91,
            "path": None,
            "url": None,
        }
    ]


@pytest.mark.asyncio
async def test_knowledge_retrieve_captures_native_chunk_locator() -> None:
    sink = InMemoryArtifactSink(
        ArtifactCaptureContext(conv_uid="conv-1", run_id="run-1")
    )
    resource = _KnowledgeResource()
    resource.name = "finance-space"
    retrieve = make_knowledge_retrieve({}, [resource])

    with bind_artifact_sink(sink):
        await retrieve("revenue")

    artifact = sink.snapshot()[0]
    assert isinstance(artifact, ReferenceArtifact)
    assert artifact.reference_type == "knowledge"
    assert artifact.source_id == "chunk-1"
    assert artifact.source_name == "annual-report.md"
    assert artifact.uri == "https://example.test/annual-report"
    assert artifact.locator == {
        "knowledge_id": "finance-space",
        "document_id": 3,
        "chunk_id": "chunk-1",
        "chunk_index": 8,
        "page": 12,
        "path": "reports/annual-report.md",
    }
    assert artifact.metadata["query_sha256"] == sha256_text("revenue")
    assert artifact.metadata["content_summary"]["excerpt_sha256"] == sha256_text(
        "Revenue grew by 20 percent."
    )


@pytest.mark.asyncio
async def test_knowledge_retrieve_empty_result_has_no_citations() -> None:
    class _EmptyResource:
        async def retrieve(self, query: str):
            return []

    retrieve = make_knowledge_retrieve({}, [_EmptyResource()])

    result = json.loads(await retrieve("missing"))

    assert result["chunks"][0]["content"] == "No relevant information found"
    assert result.get("citations", []) == []


@pytest.mark.asyncio
async def test_knowledge_retrieve_without_sink_does_not_inspect_capture_identity() -> (
    None
):
    class _DangerousIdentityResource(_KnowledgeResource):
        @property
        def name(self):
            raise AssertionError("resource identity is capture-only")

    retrieve = make_knowledge_retrieve({}, [_DangerousIdentityResource()])

    result = json.loads(await retrieve("revenue"))

    assert result["citations"][0]["id"] == "chunk-1"


@pytest.mark.asyncio
async def test_knowledge_retrieve_never_emits_non_standard_json_scores() -> None:
    class _NonFiniteScoreResource:
        async def retrieve(self, query: str):
            return [
                SimpleNamespace(
                    chunk_id="chunk-nan",
                    chunk_name="Metrics",
                    content="A finite excerpt with a non-finite upstream score.",
                    score=float("nan"),
                    metadata={"doc_name": "metrics.md"},
                )
            ]

    retrieve = make_knowledge_retrieve({}, [_NonFiniteScoreResource()])

    raw_result = await retrieve("metrics")
    result = json.loads(raw_result, parse_constant=lambda value: pytest.fail(value))

    assert "NaN" not in raw_result
    assert result["citations"][0]["score"] is None


@pytest.mark.asyncio
async def test_knowledge_retrieve_skips_empty_chunks_without_renumbering() -> None:
    class _SparseResource:
        async def retrieve(self, query: str):
            return [
                SimpleNamespace(
                    chunk_id="chunk-empty",
                    chunk_name="Empty section",
                    content="   ",
                    score=0.8,
                    metadata={"doc_name": "empty.md"},
                ),
                SimpleNamespace(
                    chunk_id="chunk-real",
                    chunk_name="Revenue section",
                    content="Revenue grew by 20 percent.",
                    score=0.91,
                    metadata={"doc_name": "annual-report.md"},
                ),
            ]

    retrieve = make_knowledge_retrieve({}, [_SparseResource()])

    result = json.loads(await retrieve("revenue"))

    assert result["chunks"][1]["content"] == "[2] Revenue grew by 20 percent."
    assert result["citations"] == [
        {
            "id": "chunk-real",
            "sourceName": "annual-report.md",
            "chunkIndex": 2,
            "score": 0.91,
            "path": None,
            "url": None,
        }
    ]
