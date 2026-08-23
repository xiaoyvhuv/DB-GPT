"""Chart Action for SQL execution and rendering."""

import json
import logging
import time
from datetime import datetime, timezone
from typing import List, Optional

from dbgpt._private.pydantic import BaseModel, Field, model_to_dict, model_to_json
from dbgpt.vis.tags.vis_chart import Vis, VisChart

from ....core.interface.report import ArtifactContent, ChartArtifact, SQLArtifact
from ....core.interface.report_capture import (
    get_current_artifact_sink,
    sha256_json,
    sha256_text,
    summarize_tabular_artifact,
)
from ...core.action.base import Action, ActionOutput
from ...resource.base import AgentResource, ResourceType
from ...resource.database import DBResource

logger = logging.getLogger(__name__)
_MAX_DATAFRAME_PREVIEW_BYTES = 256 * 1024
_MAX_DATAFRAME_PREVIEW_ROWS = 100
_MAX_CHART_PREVIEW_CHARS = 50_000
_MAX_CAPTURE_ERROR_CHARS = 8_000


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _capture_sql_dataframe(param: "SqlInput", db, data_df, duration_ms: int):
    sink = get_current_artifact_sink()
    if sink is None:
        return None
    try:
        columns = [str(column) for column in data_df.columns]
        rows = data_df.itertuples(index=False, name=None)
        result_value, complete_sha256, row_count, preview_truncated = (
            summarize_tabular_artifact(
                columns,
                rows,
                max_preview_rows=_MAX_DATAFRAME_PREVIEW_ROWS,
                max_preview_bytes=_MAX_DATAFRAME_PREVIEW_BYTES,
            )
        )
        artifact_id = sink.context.new_artifact_id("sql")
        result_json = json.dumps(
            result_value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        sink.record(
            SQLArtifact(
                artifact_id=artifact_id,
                execution=sink.context.execution_reference(action="chart_action.query"),
                created_at=datetime.now(timezone.utc),
                status="succeeded",
                duration_ms=duration_ms,
                sql=param.sql,
                database=str(getattr(db, "_db_name", "")) or None,
                display_type=param.display_type,
                columns=columns,
                row_count=row_count,
                result=ArtifactContent(
                    inline=result_value,
                    media_type="application/json",
                    sha256=sha256_json(result_value),
                    size=len(result_json.encode("utf-8")),
                ),
                metadata={
                    "input_summary": {
                        "sql_chars": len(param.sql),
                        "sql_sha256": sha256_text(param.sql),
                    },
                    "output_summary": {
                        "column_count": len(columns),
                        "row_count": row_count,
                        "complete_result_sha256": complete_sha256,
                        "preview_truncated": preview_truncated,
                    },
                },
            )
        )
        return artifact_id
    except Exception:
        logger.warning("Unable to capture ChartAction SQL artifact", exc_info=True)
        return None


def _capture_sql_failure(param: "SqlInput", db, duration_ms: int, error: str) -> None:
    sink = get_current_artifact_sink()
    if sink is None:
        return
    try:
        sink.record(
            SQLArtifact(
                artifact_id=sink.context.new_artifact_id("sql"),
                execution=sink.context.execution_reference(action="chart_action.query"),
                created_at=datetime.now(timezone.utc),
                status="failed",
                duration_ms=duration_ms,
                error=error[:_MAX_CAPTURE_ERROR_CHARS],
                sql=param.sql,
                database=str(getattr(db, "_db_name", "")) or None,
                display_type=param.display_type,
                result=None,
                metadata={
                    "input_summary": {
                        "sql_chars": len(param.sql),
                        "sql_sha256": sha256_text(param.sql),
                    },
                    "output_summary": {
                        "column_count": 0,
                        "row_count": None,
                        "error_chars": len(error),
                        "error_sha256": sha256_text(error),
                        "error_truncated": len(error) > _MAX_CAPTURE_ERROR_CHARS,
                    },
                },
            )
        )
    except Exception:
        logger.warning("Unable to capture failed ChartAction SQL", exc_info=True)


def _capture_chart_render(
    param: "SqlInput",
    sql_artifact_id: Optional[str],
    duration_ms: int,
    *,
    view: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    sink = get_current_artifact_sink()
    if sink is None or sql_artifact_id is None:
        return
    try:
        failed = error is not None
        output = None
        metadata = {}
        if not failed and view is not None:
            preview = view[:_MAX_CHART_PREVIEW_CHARS]
            output = ArtifactContent(
                inline=preview,
                media_type="application/vnd.dbgpt.vis+json",
                sha256=sha256_text(preview),
                size=len(preview.encode("utf-8")),
            )
            metadata["output_summary"] = {
                "complete_view_chars": len(view),
                "complete_view_sha256": sha256_text(view),
                "preview_truncated": len(view) > _MAX_CHART_PREVIEW_CHARS,
            }
        elif error is not None:
            metadata["output_summary"] = {
                "error_chars": len(error),
                "error_sha256": sha256_text(error),
                "error_truncated": len(error) > _MAX_CAPTURE_ERROR_CHARS,
            }
        sink.record(
            ChartArtifact(
                artifact_id=sink.context.new_artifact_id("chart"),
                execution=sink.context.execution_reference(
                    action="chart_action.render"
                ),
                input_artifact_ids=[sql_artifact_id],
                created_at=datetime.now(timezone.utc),
                status="failed" if failed else "succeeded",
                duration_ms=duration_ms,
                error=(error[:_MAX_CAPTURE_ERROR_CHARS] if error is not None else None),
                chart_type=param.display_type,
                spec={"display_type": param.display_type},
                output=output,
                metadata=metadata,
            )
        )
    except Exception:
        logger.warning("Unable to capture ChartAction chart artifact", exc_info=True)


class SqlInput(BaseModel):
    """SQL input model."""

    display_type: str = Field(
        ...,
        description="The chart rendering method selected for SQL. If you don’t know "
        "what to output, just output 'response_table' uniformly.",
    )
    sql: str = Field(
        ..., description="Executable sql generated for the current target/problem"
    )
    thought: str = Field(..., description="Summary of thoughts to the user")


class ChartAction(Action[SqlInput]):
    """Chart action class."""

    def __init__(self, **kwargs):
        """Chart action init."""
        super().__init__(**kwargs)
        self._render_protocol = VisChart()

    @property
    def resource_need(self) -> Optional[ResourceType]:
        """Return the resource type needed for the action."""
        return ResourceType.DB

    @property
    def render_protocol(self) -> Optional[Vis]:
        """Return the render protocol."""
        return self._render_protocol

    @property
    def out_model_type(self):
        """Return the output model type."""
        return SqlInput

    async def run(
        self,
        ai_message: str,
        resource: Optional[AgentResource] = None,
        rely_action_out: Optional[ActionOutput] = None,
        need_vis_render: bool = True,
        **kwargs,
    ) -> ActionOutput:
        """Perform the action."""
        try:
            param: SqlInput = self._input_convert(ai_message, SqlInput)
        except Exception as e:
            logger.exception(f"{str(e)}! \n {ai_message}")
            return ActionOutput(
                is_exe_success=False,
                content="Error:The answer is not output in the required format.",
            )
        try:
            if not self.resource_need:
                raise ValueError("The resource type is not found！")

            if not self.render_protocol:
                raise ValueError("The rendering protocol is not initialized！")

            db_resources: List[DBResource] = DBResource.from_resource(self.resource)
            if not db_resources:
                raise ValueError("The database resource is not found！")

            db = db_resources[0]
            query_started = time.perf_counter()
            try:
                data_df = await db.query_to_df(param.sql)
            except Exception as error:
                _capture_sql_failure(
                    param,
                    db,
                    _elapsed_ms(query_started),
                    str(error) or type(error).__name__,
                )
                raise
            sql_artifact_id = _capture_sql_dataframe(
                param, db, data_df, _elapsed_ms(query_started)
            )
            render_started = time.perf_counter()
            try:
                view = await self.render_protocol.display(
                    chart=json.loads(model_to_json(param)), data_df=data_df
                )
            except Exception as error:
                _capture_chart_render(
                    param,
                    sql_artifact_id,
                    _elapsed_ms(render_started),
                    error=str(error) or type(error).__name__,
                )
                raise
            _capture_chart_render(
                param,
                sql_artifact_id,
                _elapsed_ms(render_started),
                view=view,
            )

            param_dict = model_to_dict(param)
            if not data_df.empty:
                param_dict["count"] = len(data_df)

                param_dict["data"] = json.loads(
                    data_df.to_json(
                        orient="records",
                        force_ascii=False,
                        date_format="iso",
                        date_unit="s",
                    )
                )

            content = json.dumps(param_dict, ensure_ascii=False)

            return ActionOutput(
                is_exe_success=True,
                content=content,
                view=view,
                resource_type=self.resource_need.value,
                resource_value=db._db_name,
            )
        except Exception as e:
            logger.exception("Check your answers, the sql run failed！")
            return ActionOutput(
                is_exe_success=False,
                content=f"Error:Check your answers, the sql run failed!Reason:{str(e)}",
            )
