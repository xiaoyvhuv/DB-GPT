"""Thin ReAct adapter for DB-GPT's native SQL chart action."""

import json
import re
from typing import Any, Dict, Optional

from dbgpt.agent.expand.actions.chart_action import ChartAction
from dbgpt.agent.resource.database import RDBMSConnectorResource
from dbgpt.agent.resource.tool.base import tool

from .artifact_capture import connector_database_name

_DISPLAY_TYPES = {
    "response_line_chart",
    "response_bar_chart",
    "response_pie_chart",
    "response_scatter_chart",
    "response_area_chart",
    "response_heatmap_chart",
    "response_table",
}
_FORBIDDEN_SQL_PREFIXES = {
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "TRUNCATE",
    "CREATE",
    "GRANT",
    "REVOKE",
}


def _read_only_sql(sql: str) -> str:
    """Normalize leaked Markdown escapes and enforce the normal query boundary."""
    value = re.sub(r"\\([_`*])", r"\1", sql).strip().rstrip(";")
    prefix = value.upper().lstrip().split(None, 1)[0] if value else ""
    if prefix in _FORBIDDEN_SQL_PREFIXES or prefix not in {"SELECT", "WITH", "EXPLAIN"}:
        raise ValueError("仅支持只读 SELECT / WITH / EXPLAIN 查询")
    return value


def make_chart_query(
    react_state: Dict[str, Any], database_connector: Optional[Any]
):
    """Create the tool without reimplementing SQL-to-chart execution or capture."""

    @tool(
        description=(
            "使用 DB-GPT 原生图表能力执行只读 SQL 并返回可交互的图表。"
            "适用于趋势、对比、占比和分布；图表与 SQL 会作为同一条原生执行链路被记录。"
            '参数: {"sql":"SELECT ...", "display_type":"response_line_chart", '
            '"thought":"图表说明"}。'
        )
    )
    async def chart_query(
        sql: str,
        display_type: str = "response_line_chart",
        thought: str = "",
    ) -> str:
        if database_connector is None:
            return json.dumps(
                {"chunks": [{"output_type": "text", "content": "未选择数据库。"}]},
                ensure_ascii=False,
            )
        if display_type not in _DISPLAY_TYPES:
            return json.dumps(
                {
                    "chunks": [
                        {
                            "output_type": "text",
                            "content": f"不支持的原生图表类型: {display_type}",
                        }
                    ]
                },
                ensure_ascii=False,
            )
        try:
            normalized_sql = _read_only_sql(sql)
            artifact_sink = react_state.get("_report_artifact_sink")
            database_name = connector_database_name(database_connector, artifact_sink)
            resource = RDBMSConnectorResource(
                name=database_name or "database",
                connector=database_connector,
                db_name=database_name,
            )
            action = ChartAction()
            action.init_resource(resource)
            result = await action.run(
                json.dumps(
                    {
                        "sql": normalized_sql,
                        "display_type": display_type,
                        "thought": thought or "展示数据库分析图表",
                    },
                    ensure_ascii=False,
                )
            )
            if not result.is_exe_success:
                return json.dumps(
                    {
                        "chunks": [
                            {
                                "output_type": "text",
                                "content": result.content or "原生图表生成失败。",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "chunks": [
                        {
                            "output_type": "markdown",
                            "content": result.view or result.content,
                        }
                    ]
                },
                ensure_ascii=False,
            )
        except Exception as error:
            return json.dumps(
                {
                    "chunks": [
                        {
                            "output_type": "text",
                            "content": f"原生图表生成失败: {error}",
                        }
                    ]
                },
                ensure_ascii=False,
            )

    return chart_query
