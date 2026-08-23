"""knowledge_retrieve tool — search the knowledge base."""

import json
import math
import time
from typing import Any, Dict, List

from dbgpt.agent.resource.tool.base import tool
from dbgpt.core.interface.report_capture import (
    capture_reference_artifact,
    get_current_artifact_sink,
    sha256_text,
)


def make_knowledge_retrieve(react_state: Dict[str, Any], knowledge_resources: List):
    @tool(
        description=(
            "Retrieve relevant information from the knowledge base. "
            "Use this tool when the user question involves content that may be "
            'in the knowledge base. Parameters: {{"query": "search query"}}'
        )
    )
    async def knowledge_retrieve(query: str) -> str:
        if not knowledge_resources:
            return json.dumps(
                {
                    "chunks": [
                        {
                            "output_type": "text",
                            "content": "No knowledge base available",
                        }
                    ],
                    "citations": [],
                },
                ensure_ascii=False,
            )

        resource = knowledge_resources[0]
        try:
            capture_enabled = get_current_artifact_sink() is not None
            started = time.perf_counter() if capture_enabled else None
            chunks = await resource.retrieve(query)
            duration_ms = (
                max(0, int((time.perf_counter() - started) * 1000))
                if started is not None
                else 0
            )
            if chunks:
                # Cap per-chunk and total size so a few large chunks can't
                # overflow the LLM context window. Larger outputs are
                # persisted to disk by the ToolResultStorage layer.
                MAX_CHUNK_CHARS = 4000
                MAX_TOTAL_CHARS = 20_000
                content_parts = []
                citations = []
                total_chars = 0
                for i, chunk in enumerate(chunks[:5]):
                    chunk_content = (chunk.content or "")[:MAX_CHUNK_CHARS]
                    if not chunk_content.strip():
                        continue
                    if total_chars + len(chunk_content) > MAX_TOTAL_CHARS:
                        content_parts.append(
                            f"[{i + 1}] ... [truncated: total output cap "
                            f"{MAX_TOTAL_CHARS} chars reached]"
                        )
                        break
                    content_parts.append(f"[{i + 1}] {chunk_content}")
                    total_chars += len(chunk_content)
                    metadata = (
                        chunk.metadata if isinstance(chunk.metadata, dict) else {}
                    )
                    path = metadata.get("file_path") or metadata.get("path")
                    source_name = (
                        metadata.get("doc_name")
                        or metadata.get("document_name")
                        or metadata.get("doc")
                        or metadata.get("source")
                        or path
                        or getattr(chunk, "chunk_name", None)
                        or "Knowledge Base"
                    )
                    url = metadata.get("url") or metadata.get("source_url")
                    score = getattr(chunk, "score", None)
                    finite_score = (
                        float(score)
                        if isinstance(score, (int, float))
                        and math.isfinite(float(score))
                        else None
                    )
                    citations.append(
                        {
                            "id": str(
                                getattr(chunk, "chunk_id", None)
                                or metadata.get("chunk_id")
                                or f"knowledge-{i + 1}"
                            ),
                            "sourceName": str(source_name),
                            "chunkIndex": i + 1,
                            "score": finite_score,
                            "path": str(path) if path else None,
                            "url": str(url) if url else None,
                        }
                    )
                    if capture_enabled:
                        capture_metadata = metadata
                        prop_field = metadata.get("prop_field")
                        if isinstance(prop_field, dict):
                            capture_metadata = {**prop_field, **metadata}
                        capture_path = capture_metadata.get(
                            "file_path"
                        ) or capture_metadata.get("path")
                        capture_source_name = (
                            capture_metadata.get("doc_name")
                            or capture_metadata.get("document_name")
                            or capture_metadata.get("doc")
                            or capture_metadata.get("source")
                            or capture_path
                            or getattr(chunk, "chunk_name", None)
                            or "Knowledge Base"
                        )
                        capture_url = capture_metadata.get(
                            "url"
                        ) or capture_metadata.get("source_url")
                        chunk_id = str(
                            getattr(chunk, "chunk_id", None)
                            or capture_metadata.get("chunk_id")
                            or f"knowledge-{i + 1}"
                        )
                        document_id = (
                            capture_metadata.get("document_id")
                            or capture_metadata.get("doc_id")
                            or capture_metadata.get("file_id")
                        )
                        page = capture_metadata.get("page")
                        if page is None:
                            page = capture_metadata.get("page_number")
                        knowledge_id = capture_metadata.get(
                            "space_id"
                        ) or capture_metadata.get("knowledge_id")
                        if knowledge_id is None:
                            try:
                                knowledge_id = getattr(
                                    resource, "name", None
                                ) or getattr(resource, "value", None)
                            except Exception:
                                knowledge_id = None
                        kb_uri = (
                            f"kb://{knowledge_id or 'unknown'}/"
                            f"{document_id or 'unknown'}/{chunk_id}"
                        )
                        capture_reference_artifact(
                            reference_type="knowledge",
                            source_id=chunk_id,
                            action="knowledge_retrieve",
                            duration_ms=duration_ms,
                            source_name=capture_source_name,
                            uri=str(capture_url) if capture_url else kb_uri,
                            locator={
                                "knowledge_id": knowledge_id,
                                "document_id": document_id,
                                "chunk_id": chunk_id,
                                "chunk_index": capture_metadata.get("chunk_index", i),
                                "page": page,
                                "path": (str(capture_path) if capture_path else None),
                            },
                            excerpt=chunk.content or "",
                            score=finite_score,
                            metadata={"query_sha256": sha256_text(query)},
                        )
                content = "\n".join(content_parts)
                return json.dumps(
                    {
                        "chunks": [
                            {
                                "output_type": "text",
                                "content": (
                                    f"Retrieved {len(chunks)} relevant documents"
                                ),
                            },
                            {"output_type": "markdown", "content": content},
                        ],
                        "citations": citations,
                    },
                    ensure_ascii=False,
                    allow_nan=False,
                )
            else:
                return json.dumps(
                    {
                        "chunks": [
                            {
                                "output_type": "text",
                                "content": "No relevant information found",
                            }
                        ],
                        "citations": [],
                    },
                    ensure_ascii=False,
                )
        except Exception as e:
            return json.dumps(
                {
                    "chunks": [
                        {
                            "output_type": "text",
                            "content": f"Knowledge retrieval failed: {str(e)}",
                        }
                    ],
                    "citations": [],
                },
                ensure_ascii=False,
            )

    return knowledge_retrieve
