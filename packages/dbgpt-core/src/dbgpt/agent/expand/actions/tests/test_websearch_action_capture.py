"""Tests for web evidence captured by the native WebSearchAction."""

import pytest
import requests

from dbgpt.agent.expand.actions import websearch_action as web_module
from dbgpt.agent.expand.actions.websearch_action import WebSearchAction, WebSearchInput
from dbgpt.core.interface.report import ReferenceArtifact
from dbgpt.core.interface.report_capture import (
    ArtifactCaptureContext,
    InMemoryArtifactSink,
    bind_artifact_sink,
    sha256_text,
)


@pytest.mark.asyncio
async def test_web_search_captures_url_fetch_time_and_content_hash(monkeypatch) -> None:
    action = WebSearchAction()
    monkeypatch.setattr(
        action,
        "_input_convert",
        lambda *_args: WebSearchInput(
            is_need="yes", keywords="DB-GPT report", thought="search"
        ),
    )
    content = "Evidence fetched from the page."
    monkeypatch.setattr(
        web_module,
        "get_bing_search_results",
        lambda *_args, **_kwargs: [
            {
                "title": "DB-GPT",
                "link": "https://user:pass@example.test/dbgpt?token=secret",
                "snippet": "A search snippet",
                "content": content,
                "_report_requested_url": (
                    "https://user:pass@example.test/dbgpt?token=secret"
                ),
                "_report_final_url": ("https://example.test/final?sig=secret&lang=en"),
                "_report_fetch_started_at": "2026-08-24T08:00:00+00:00",
                "_report_fetched_at": "2026-08-24T08:00:00.037000+00:00",
                "_report_fetch_duration_ms": 37,
                "_report_fetch_status": "succeeded",
            }
        ],
    )
    sink = InMemoryArtifactSink(
        ArtifactCaptureContext(conv_uid="conv-1", run_id="run-1")
    )

    with bind_artifact_sink(sink):
        result = await action.run("ignored")

    assert result.is_exe_success
    artifact = sink.snapshot()[0]
    assert isinstance(artifact, ReferenceArtifact)
    assert artifact.reference_type == "web"
    assert artifact.uri == "https://example.test/final?sig=%5BREDACTED%5D&lang=en"
    assert artifact.duration_ms == 37
    assert artifact.locator["requested_url"] == (
        "https://example.test/dbgpt?token=%5BREDACTED%5D"
    )
    assert artifact.locator["final_url"] == artifact.uri
    assert artifact.locator["fetch_started_at"] == "2026-08-24T08:00:00+00:00"
    assert artifact.locator["fetched_at"] == "2026-08-24T08:00:00.037000+00:00"
    assert artifact.metadata["content_sha256"] == sha256_text(content)
    assert artifact.metadata["content_summary"]["excerpt_sha256"] == sha256_text(
        content
    )


@pytest.mark.asyncio
async def test_web_search_uses_snippet_when_page_fetch_failed(monkeypatch) -> None:
    action = WebSearchAction()
    monkeypatch.setattr(
        action,
        "_input_convert",
        lambda *_args: WebSearchInput(
            is_need="yes", keywords="DB-GPT report", thought="search"
        ),
    )
    monkeypatch.setattr(
        web_module,
        "get_bing_search_results",
        lambda *_args, **_kwargs: [
            {
                "title": "DB-GPT",
                "link": "https://example.test/dbgpt",
                "snippet": "Search-index evidence",
                "content": "无法获取内容",
                "_report_requested_url": "https://example.test/dbgpt",
                "_report_final_url": "https://example.test/dbgpt",
                "_report_fetch_started_at": "2026-08-24T08:00:00+00:00",
                "_report_fetched_at": "2026-08-24T08:00:00.012000+00:00",
                "_report_fetch_duration_ms": 12,
                "_report_fetch_status": "failed",
            }
        ],
    )
    sink = InMemoryArtifactSink(
        ArtifactCaptureContext(conv_uid="conv-1", run_id="run-1")
    )

    with bind_artifact_sink(sink):
        await action.run("ignored")

    artifact = sink.snapshot()[0]
    assert artifact.excerpt == "Search-index evidence"
    assert artifact.status == "failed"
    assert artifact.error == "page fetch failed"
    assert artifact.metadata["fetch_status"] == "failed"
    assert artifact.metadata["content_sha256"] == sha256_text("Search-index evidence")


def test_get_page_content_records_real_request_provenance(monkeypatch) -> None:
    class FakeResponse:
        headers = {"Content-Type": "text/html; charset=utf-8"}
        url = "https://example.test/final"

        def raise_for_status(self) -> None:
            return None

        def iter_content(self, chunk_size: int):
            assert chunk_size == 1024
            yield b"<html><body><p>Fetched evidence text.</p></body></html>"

        def close(self) -> None:
            return None

    monkeypatch.setattr(requests, "get", lambda *_args, **_kwargs: FakeResponse())
    provenance = {}

    content = web_module.get_page_content(
        "https://example.test/requested", provenance=provenance
    )

    assert content == "Fetched evidence text."
    assert provenance["requested_url"] == "https://example.test/requested"
    assert provenance["final_url"] == "https://example.test/final"
    assert provenance["fetch_status"] == "succeeded"
    assert provenance["fetch_started_at"].endswith("+00:00")
    assert provenance["fetched_at"].endswith("+00:00")
    assert isinstance(provenance["fetch_duration_ms"], int)
