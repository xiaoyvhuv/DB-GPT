"""Tests for the safe HTML report preview."""

from dbgpt.core.interface.report import TableBlock
from dbgpt_serve.report.rendering import (
    MAX_RENDER_CELL_CHARS,
    MAX_RENDER_TABLE_ROWS,
    render_report_html,
)

from .test_dao import _document


def test_html_preview_escapes_content_and_has_no_script_capability() -> None:
    document = _document(title='<script>alert("x")</script>')

    rendered = render_report_html(document)

    assert "<script>" not in rendered
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in rendered
    assert "Content-Security-Policy" in rendered
    assert "default-src &#x27;none&#x27;" in rendered
    assert 'data-block-id="metric-gmv"' in rendered


def test_rendering_bounds_table_rows_and_cell_content() -> None:
    document = _document()
    sql = document.artifacts[0]
    sql.result.inline = [
        {"gmv": f"row-{index}-" + "x" * (MAX_RENDER_CELL_CHARS + 10)}
        for index in range(MAX_RENDER_TABLE_ROWS + 1)
    ]
    document.sections[0].blocks.append(
        TableBlock(block_id="table-gmv", data_artifact_id="sql-1")
    )

    rendered = render_report_html(document)

    assert f"row-{MAX_RENDER_TABLE_ROWS - 1}-" in rendered
    assert f"row-{MAX_RENDER_TABLE_ROWS}-" not in rendered
    assert "x" * (MAX_RENDER_CELL_CHARS + 1) not in rendered
