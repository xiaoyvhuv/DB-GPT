"""sql_query tool — read-only SQL query against the selected database."""

import json
import re
import time
from typing import Any, Dict, Optional

from dbgpt.agent.resource.tool.base import tool
from dbgpt.core.interface.report_capture import normalize_artifact_value

from .artifact_capture import capture_sql_execution, connector_database_name


def _normalize_sql_markdown_escapes(sql: str) -> str:
    """Undo Markdown escaping that some models leak into SQL identifiers."""
    return re.sub(r"\\([_`*])", r"\1", sql)


def _remember_sql_result(
    react_state: Dict[str, Any],
    *,
    artifact_id: Optional[str],
    sql: str,
    columns: list[str],
    rows: Any,
) -> None:
    """Expose bounded, JSON-safe real SQL outputs to later Python steps."""
    if not artifact_id:
        return
    normalized_rows = [normalize_artifact_value(row) for row in list(rows)[:500]]
    entry = {
        "artifact_id": artifact_id,
        "sql": sql,
        "columns": columns,
        "rows": normalized_rows,
        "row_count": len(rows),
        "truncated": len(rows) > len(normalized_rows),
    }
    history = react_state.setdefault("_report_sql_results", [])
    if isinstance(history, list):
        history.append(entry)
        del history[:-8]


def make_sql_query(
    react_state: Dict[str, Any],
    database_connector: Optional[Any],
    *,
    max_output_chars: Optional[int] = 20_000,
):
    @tool(
        description=(
            "对用户选择的数据库执行 SQL 查询（仅支持 SELECT）。"
            '参数: {"sql": "SELECT 语句"}'
        )
    )
    def sql_query(sql: str) -> str:
        """Execute a read-only SQL query against the selected database."""
        artifact_sink = react_state.get("_report_artifact_sink")
        if database_connector is None:
            return json.dumps(
                {
                    "chunks": [
                        {
                            "output_type": "text",
                            "content": "未选择数据库，请先在左侧面板选择一个数据源。",
                        }
                    ]
                },
                ensure_ascii=False,
            )

        sql_stripped = _normalize_sql_markdown_escapes(sql).strip().rstrip(";")
        sql_upper = sql_stripped.upper().lstrip()
        forbidden = [
            "INSERT",
            "UPDATE",
            "DELETE",
            "DROP",
            "ALTER",
            "TRUNCATE",
            "CREATE",
            "GRANT",
            "REVOKE",
        ]
        for kw in forbidden:
            if sql_upper.startswith(kw):
                return json.dumps(
                    {
                        "chunks": [
                            {
                                "output_type": "text",
                                "content": f"安全限制: 不允许执行 {kw} 语句，"
                                "仅支持 SELECT 查询。",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )

        started = time.perf_counter()
        try:
            result = database_connector.run(sql_stripped)
            if not result:
                capture_sql_execution(
                    sql=sql_stripped,
                    database=connector_database_name(
                        database_connector, artifact_sink
                    ),
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    columns=[],
                    rows=[],
                    artifact_sink=artifact_sink,
                )
                return json.dumps(
                    {
                        "chunks": [
                            {"output_type": "text", "content": "查询返回空结果。"}
                        ]
                    },
                    ensure_ascii=False,
                )

            columns = result[0]
            col_names = [str(c[0]) if isinstance(c, tuple) else str(c) for c in columns]
            rows = result[1:]

            artifact_id = capture_sql_execution(
                sql=sql_stripped,
                database=connector_database_name(database_connector, artifact_sink),
                duration_ms=int((time.perf_counter() - started) * 1000),
                columns=col_names,
                rows=rows,
                artifact_sink=artifact_sink,
            )
            _remember_sql_result(
                react_state,
                artifact_id=artifact_id,
                sql=sql_stripped,
                columns=col_names,
                rows=rows,
            )

            header = "| " + " | ".join(col_names) + " |"
            separator = "| " + " | ".join(["---"] * len(col_names)) + " |"
            md_rows = []
            for row in rows[:50]:
                md_rows.append("| " + " | ".join(str(v) for v in row) + " |")
            table = "\n".join([header, separator] + md_rows)
            if len(rows) > 50:
                table += f"\n\n（仅显示前 50 行，共 {len(rows)} 行）"

            # Cap total output size so a single wide query can't blow out the
            # LLM context window. The full result remains available via the
            # ToolResultStorage persistence layer if it exceeds the threshold.
            if max_output_chars is not None and len(table) > max_output_chars:
                table = (
                    table[:max_output_chars]
                    + f"\n\n... [Output truncated at {max_output_chars} chars. "
                    f"Total rows: {len(rows)}]"
                )

            return json.dumps(
                {"chunks": [{"output_type": "markdown", "content": table}]},
                ensure_ascii=False,
            )
        except Exception as e:
            capture_sql_execution(
                sql=sql_stripped,
                database=connector_database_name(database_connector, artifact_sink),
                duration_ms=int((time.perf_counter() - started) * 1000),
                error=str(e) or type(e).__name__,
                artifact_sink=artifact_sink,
            )
            return json.dumps(
                {
                    "chunks": [
                        {
                            "output_type": "text",
                            "content": f"SQL 执行失败: {str(e)}",
                        }
                    ]
                },
                ensure_ascii=False,
            )

    return sql_query
