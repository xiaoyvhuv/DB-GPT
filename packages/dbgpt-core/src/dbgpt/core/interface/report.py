"""Versioned report and execution-evidence protocol.

The models in this module are cross-layer wire contracts. They describe
executed artifacts and their lineage; they do not expose model reasoning and
do not perform execution, persistence, or rendering.
"""

from datetime import datetime
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from dbgpt._private.pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

MAX_REPORT_ID_LENGTH = 64
MAX_REPORT_TITLE_LENGTH = 512
MAX_CONV_UID_LENGTH = 255
MAX_RUN_ID_LENGTH = 64
MAX_ARTIFACT_ID_LENGTH = 128
MAX_STEP_ID_LENGTH = 255
MAX_MESSAGE_ID_LENGTH = 255
MAX_TASK_ID_LENGTH = 64
MAX_SECTION_ID_LENGTH = 128
MAX_BLOCK_ID_LENGTH = 128


class _ProtocolModel(BaseModel):
    """Strict base for stable protocol payloads."""

    model_config = ConfigDict(extra="forbid")


def _require_non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


def _require_max_length(name: str, value: str, max_length: int) -> None:
    if len(value) > max_length:
        raise ValueError(f"{name} exceeds {max_length} characters")


class ExecutionReference(_ProtocolModel):
    """Locate the DB-GPT execution that produced an artifact."""

    conv_uid: str
    step_id: str
    message_id: Optional[str] = None
    task_id: Optional[str] = None
    run_id: Optional[str] = None
    action: Optional[str] = None

    _validate_conv_uid = field_validator("conv_uid")(_require_non_blank)
    _validate_step_id = field_validator("step_id")(_require_non_blank)


class ArtifactContent(_ProtocolModel):
    """Inline artifact content, durable content URI, or both."""

    inline: Optional[Any] = None
    uri: Optional[str] = None
    media_type: Optional[str] = None
    sha256: Optional[str] = None
    size: Optional[int] = Field(default=None, ge=0)

    @field_validator("uri")
    @classmethod
    def validate_uri(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            _require_non_blank(value)
        return value

    @model_validator(mode="after")
    def validate_location(self) -> "ArtifactContent":
        if self.inline is None and self.uri is None:
            raise ValueError("artifact content requires inline or uri")
        return self


class BaseArtifact(_ProtocolModel):
    """Fields shared by all executed artifact variants."""

    artifact_id: str
    name: Optional[str] = None
    execution: ExecutionReference
    input_artifact_ids: List[str] = Field(default_factory=list)
    created_at: datetime
    status: Optional[Literal["succeeded", "failed", "cancelled", "timed_out"]] = None
    duration_ms: Optional[int] = Field(default=None, ge=0)
    error: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    _validate_artifact_id = field_validator("artifact_id")(_require_non_blank)

    @field_validator("input_artifact_ids")
    @classmethod
    def validate_input_artifact_ids(cls, values: List[str]) -> List[str]:
        for value in values:
            _require_non_blank(value)
        return values

    @field_validator("error")
    @classmethod
    def validate_error(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            _require_non_blank(value)
        return value

    @model_validator(mode="after")
    def validate_outcome(self) -> "BaseArtifact":
        if self.status == "succeeded" and self.error is not None:
            raise ValueError("successful artifact must not contain an error")
        if self.status in {"failed", "cancelled", "timed_out"} and self.error is None:
            raise ValueError("non-successful artifact requires an error")
        return self

    @model_serializer(mode="wrap")
    def serialize_versioned_fields(self, handler):
        """Keep legacy v1 artifact JSON free of absent v2-only fields."""
        data = handler(self)
        for field_name in ("status", "duration_ms", "error", "stderr"):
            if data.get(field_name) is None:
                data.pop(field_name, None)
        return data


class SQLArtifact(BaseArtifact):
    """A read-only SQL execution and its result."""

    type: Literal["sql"] = "sql"
    sql: str
    database: Optional[str] = None
    display_type: Optional[str] = None
    columns: List[str] = Field(default_factory=list)
    row_count: Optional[int] = Field(default=None, ge=0)
    result: Optional[ArtifactContent] = None

    _validate_sql = field_validator("sql")(_require_non_blank)

    @model_validator(mode="after")
    def validate_result(self) -> "SQLArtifact":
        if self.status in {None, "succeeded"} and self.result is None:
            raise ValueError("successful SQL artifact requires result")
        return self


class PythonArtifact(BaseArtifact):
    """Executed Python source, status, logs, and optional result."""

    type: Literal["python"] = "python"
    code: str
    language: str = "python"
    exit_code: Optional[int]
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    result: Optional[ArtifactContent] = None

    _validate_code = field_validator("code")(_require_non_blank)
    _validate_language = field_validator("language")(_require_non_blank)

    @model_validator(mode="after")
    def validate_process_state(self) -> "PythonArtifact":
        if self.status is None:
            if self.exit_code is None:
                raise ValueError("legacy Python artifact requires an exit code")
            return self
        if self.status == "succeeded" and self.exit_code != 0:
            raise ValueError("successful Python artifact requires exit code 0")
        if self.status in {"cancelled", "timed_out"} and self.exit_code is not None:
            raise ValueError(
                f"{self.status} Python artifact must not contain an exit code"
            )
        if self.status == "failed" and self.exit_code == 0:
            raise ValueError("failed Python artifact cannot contain exit code 0")
        return self


class ChartArtifact(BaseArtifact):
    """A rendered chart backed by SQL or Python lineage."""

    type: Literal["chart"] = "chart"
    chart_type: str
    spec: Dict[str, Any] = Field(default_factory=dict)
    output: ArtifactContent

    _validate_chart_type = field_validator("chart_type")(_require_non_blank)


class ReferenceArtifact(BaseArtifact):
    """A knowledge, memory, web, or model reference used by an execution."""

    type: Literal["reference"] = "reference"
    reference_type: Literal["knowledge", "memory", "web", "model"]
    source_id: str
    source_name: Optional[str] = None
    uri: Optional[str] = None
    locator: Dict[str, Any] = Field(default_factory=dict)
    excerpt: Optional[str] = None
    score: Optional[float] = Field(default=None, ge=0)

    _validate_source_id = field_validator("source_id")(_require_non_blank)


Artifact = Annotated[
    Union[SQLArtifact, PythonArtifact, ChartArtifact, ReferenceArtifact],
    Field(discriminator="type"),
]


class EvidenceReference(_ProtocolModel):
    """Bind a report claim to a location in an executed artifact."""

    artifact_id: str
    role: Literal["source", "calculation", "support"]
    selector: Dict[str, Any] = Field(default_factory=dict)
    target_selector: Dict[str, Any] = Field(default_factory=dict)

    _validate_artifact_id = field_validator("artifact_id")(_require_non_blank)


class BaseReportBlock(_ProtocolModel):
    """Fields shared by all report block variants."""

    block_id: str
    title: Optional[str] = None
    evidence: List[EvidenceReference] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    _validate_block_id = field_validator("block_id")(_require_non_blank)


class MetricBlock(BaseReportBlock):
    """A scalar value rendered as a report metric."""

    type: Literal["metric"] = "metric"
    value: Any
    display_value: Optional[str] = None
    unit: Optional[str] = None

    @model_validator(mode="after")
    def validate_evidence(self) -> "MetricBlock":
        if not self.evidence:
            raise ValueError("metric block requires evidence")
        return self


class TableBlock(BaseReportBlock):
    """A table rendered from a SQL or Python artifact result."""

    type: Literal["table"] = "table"
    data_artifact_id: str
    description: Optional[str] = None

    _validate_data_artifact_id = field_validator("data_artifact_id")(_require_non_blank)


class ChartBlock(BaseReportBlock):
    """A chart rendered from a chart artifact."""

    type: Literal["chart"] = "chart"
    chart_artifact_id: str
    description: Optional[str] = None

    _validate_chart_artifact_id = field_validator("chart_artifact_id")(
        _require_non_blank
    )


class InsightBlock(BaseReportBlock):
    """A derived natural-language conclusion with supporting evidence."""

    type: Literal["insight"] = "insight"
    content: str

    _validate_content = field_validator("content")(_require_non_blank)

    @model_validator(mode="after")
    def validate_evidence(self) -> "InsightBlock":
        if not self.evidence:
            raise ValueError("insight block requires evidence")
        return self


class TextBlock(BaseReportBlock):
    """Explanatory report text that may optionally carry evidence."""

    type: Literal["text"] = "text"
    content: str

    _validate_content = field_validator("content")(_require_non_blank)


ReportBlock = Annotated[
    Union[MetricBlock, TableBlock, ChartBlock, InsightBlock, TextBlock],
    Field(discriminator="type"),
]


class ReportSection(_ProtocolModel):
    """An ordered group of report blocks."""

    section_id: str
    title: str
    blocks: List[ReportBlock] = Field(default_factory=list)

    _validate_section_id = field_validator("section_id")(_require_non_blank)
    _validate_title = field_validator("title")(_require_non_blank)


class ReportDocument(_ProtocolModel):
    """A versioned report and its self-contained evidence registry."""

    schema_version: Literal[1, 2] = 1
    report_id: str
    title: str
    conv_uid: str
    created_at: datetime
    sections: List[ReportSection] = Field(default_factory=list)
    artifacts: List[Artifact] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    _validate_report_id = field_validator("report_id")(_require_non_blank)
    _validate_title = field_validator("title")(_require_non_blank)
    _validate_conv_uid = field_validator("conv_uid")(_require_non_blank)

    @model_validator(mode="after")
    def validate_integrity(self) -> "ReportDocument":
        """Validate IDs, references, and the artifact dependency graph."""
        if self.schema_version == 1:
            for artifact in self.artifacts:
                has_v2_fields = any(
                    value is not None
                    for value in (
                        artifact.status,
                        artifact.duration_ms,
                        artifact.error,
                    )
                )
                if isinstance(artifact, PythonArtifact):
                    has_v2_fields = has_v2_fields or artifact.stderr is not None
                if has_v2_fields:
                    raise ValueError(
                        "artifact execution outcome requires schema version 2"
                    )
        else:
            self._validate_v2_persistence_lengths()
            for artifact in self.artifacts:
                if artifact.status is None or artifact.duration_ms is None:
                    raise ValueError(
                        "schema version 2 artifacts require status and duration"
                    )
                if artifact.execution.conv_uid != self.conv_uid:
                    raise ValueError(
                        f"artifact conversation {artifact.execution.conv_uid} does "
                        f"not match report conversation {self.conv_uid}"
                    )

        artifact_ids = [artifact.artifact_id for artifact in self.artifacts]
        duplicate = _find_duplicate(artifact_ids)
        if duplicate is not None:
            raise ValueError(f"duplicate artifact id: {duplicate}")

        section_ids = [section.section_id for section in self.sections]
        duplicate = _find_duplicate(section_ids)
        if duplicate is not None:
            raise ValueError(f"duplicate section id: {duplicate}")

        blocks = [block for section in self.sections for block in section.blocks]
        duplicate = _find_duplicate([block.block_id for block in blocks])
        if duplicate is not None:
            raise ValueError(f"duplicate block id: {duplicate}")

        artifacts = {artifact.artifact_id: artifact for artifact in self.artifacts}
        for artifact in self.artifacts:
            for input_id in artifact.input_artifact_ids:
                if input_id not in artifacts:
                    raise ValueError(f"unknown input artifact {input_id}")

        self._validate_acyclic_artifacts(artifacts)

        for block in blocks:
            for evidence in block.evidence:
                if evidence.artifact_id not in artifacts:
                    raise ValueError(f"unknown artifact {evidence.artifact_id}")

            if isinstance(block, ChartBlock):
                target = artifacts.get(block.chart_artifact_id)
                if not isinstance(target, ChartArtifact):
                    raise ValueError(
                        f"chart block {block.block_id} requires a chart artifact"
                    )
            elif isinstance(block, TableBlock):
                target = artifacts.get(block.data_artifact_id)
                if not isinstance(target, (SQLArtifact, PythonArtifact)):
                    raise ValueError(
                        f"table block {block.block_id} requires a SQL or Python "
                        "artifact"
                    )

        for artifact in self.artifacts:
            if isinstance(artifact, ChartArtifact) and not self._has_analysis_lineage(
                artifact, artifacts
            ):
                raise ValueError(
                    f"chart artifact {artifact.artifact_id} requires SQL or Python "
                    "lineage"
                )
        return self

    def _validate_v2_persistence_lengths(self) -> None:
        """Ensure new snapshots fit the shared relational persistence contract."""
        _require_max_length("report_id", self.report_id, MAX_REPORT_ID_LENGTH)
        _require_max_length("title", self.title, MAX_REPORT_TITLE_LENGTH)
        _require_max_length("conv_uid", self.conv_uid, MAX_CONV_UID_LENGTH)

        for section in self.sections:
            _require_max_length("section_id", section.section_id, MAX_SECTION_ID_LENGTH)
            for block in section.blocks:
                _require_max_length("block_id", block.block_id, MAX_BLOCK_ID_LENGTH)

        for artifact in self.artifacts:
            _require_max_length(
                "artifact_id", artifact.artifact_id, MAX_ARTIFACT_ID_LENGTH
            )
            execution = artifact.execution
            _require_max_length(
                "execution.conv_uid", execution.conv_uid, MAX_CONV_UID_LENGTH
            )
            _require_max_length(
                "execution.step_id", execution.step_id, MAX_STEP_ID_LENGTH
            )
            for name, value, max_length in (
                ("execution.message_id", execution.message_id, MAX_MESSAGE_ID_LENGTH),
                ("execution.task_id", execution.task_id, MAX_TASK_ID_LENGTH),
                ("execution.run_id", execution.run_id, MAX_RUN_ID_LENGTH),
            ):
                if value is not None:
                    _require_max_length(name, value, max_length)

    @staticmethod
    def _validate_acyclic_artifacts(artifacts: Dict[str, BaseArtifact]) -> None:
        visited = set()
        visiting = set()

        def visit(artifact_id: str) -> None:
            if artifact_id in visiting:
                raise ValueError(f"artifact dependency cycle: {artifact_id}")
            if artifact_id in visited:
                return
            visiting.add(artifact_id)
            for input_id in artifacts[artifact_id].input_artifact_ids:
                visit(input_id)
            visiting.remove(artifact_id)
            visited.add(artifact_id)

        for artifact_id in artifacts:
            visit(artifact_id)

    @staticmethod
    def _has_analysis_lineage(
        artifact: BaseArtifact, artifacts: Dict[str, BaseArtifact]
    ) -> bool:
        for input_id in artifact.input_artifact_ids:
            input_artifact = artifacts[input_id]
            if isinstance(input_artifact, (SQLArtifact, PythonArtifact)):
                return True
            if ReportDocument._has_analysis_lineage(input_artifact, artifacts):
                return True
        return False


def _find_duplicate(values: List[str]) -> Optional[str]:
    seen = set()
    for value in values:
        if value in seen:
            return value
        seen.add(value)
    return None


__all__ = [
    "Artifact",
    "ArtifactContent",
    "ChartArtifact",
    "ChartBlock",
    "EvidenceReference",
    "ExecutionReference",
    "InsightBlock",
    "MAX_ARTIFACT_ID_LENGTH",
    "MAX_BLOCK_ID_LENGTH",
    "MAX_CONV_UID_LENGTH",
    "MAX_MESSAGE_ID_LENGTH",
    "MAX_REPORT_ID_LENGTH",
    "MAX_REPORT_TITLE_LENGTH",
    "MAX_RUN_ID_LENGTH",
    "MAX_SECTION_ID_LENGTH",
    "MAX_STEP_ID_LENGTH",
    "MAX_TASK_ID_LENGTH",
    "MetricBlock",
    "PythonArtifact",
    "ReferenceArtifact",
    "ReportBlock",
    "ReportDocument",
    "ReportSection",
    "SQLArtifact",
    "TableBlock",
    "TextBlock",
]
