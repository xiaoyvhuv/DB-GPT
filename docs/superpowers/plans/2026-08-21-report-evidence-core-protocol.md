# Report and Evidence Core Protocol Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a versioned, strictly validated Report and Evidence protocol to `dbgpt-core` without changing existing execution, persistence, API, or frontend behavior.

**Architecture:** Define Pydantic models in `dbgpt.core.interface.report`, using discriminated unions for artifacts and report blocks. Keep producer-specific data in explicit mappings, then validate report-wide IDs, references, artifact types, and dependency topology in `ReportDocument`. Export the stable contract through `dbgpt.core` for later App and Serve adapters.

**Tech Stack:** Python 3.10, DB-GPT's `dbgpt._private.pydantic` compatibility layer, Pydantic 2, pytest, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-08-21-report-evidence-core-protocol-design.md`

## Global Constraints

- Work only in `packages/dbgpt-core`; do not modify App, Serve, web, actions, SSE events, persistence, or legacy `ReportData`.
- Use `dbgpt._private.pydantic`; do not add dependencies or change `uv.lock`.
- Require caller-supplied stable IDs; do not generate UUIDs in protocol models.
- Set `schema_version` to integer `1` and reject undeclared top-level fields.
- SQL, Python, and chart artifacts are typed; knowledge, memory, web, and model sources use the generic reference artifact.
- The evidence graph records execution lineage, never hidden model reasoning or chain of thought.
- Report blocks have five variants: metric, table, chart, insight, and text.
- Evidence keeps source `selector` and report-side `target_selector` separate so one metric embedded in a multi-metric text block can be addressed independently.
- Validation is structural and must not execute SQL, access files or URIs, compute hashes, or judge insight truth.
- Run every Python command through `conda run -n dbgpt_env` so checks use `E:\software\Anaconda\envs\dbgpt_env`.
- Do not run the repository `make` targets on Windows: the current Makefile creates `.venv.make` and assumes Unix `bin/activate`.
- Before each commit, manually run the task's pytest and Ruff commands. Use `git commit --no-verify` only because the installed hook invokes the incompatible Makefile; record the fresh manual verification immediately before it.

---

## File Structure

- Create `packages/dbgpt-core/src/dbgpt/core/interface/report.py`: all version-1 protocol models, discriminated unions, and report-wide structural validation.
- Create `packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report.py`: behavioral contract and validation tests using real Pydantic models, with no mocks.
- Modify `packages/dbgpt-core/src/dbgpt/core/__init__.py`: public imports and `__ALL__` entries for the protocol.

The protocol stays in one focused module because all types form one versioned wire contract and must evolve together. Persistence and adapters remain separate in later PRs.

### Task 1: Execution, content, and artifact models

**Files:**
- Create: `packages/dbgpt-core/src/dbgpt/core/interface/report.py`
- Create: `packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report.py`

**Interfaces:**
- Produces: `ExecutionReference`, `ArtifactContent`, `SQLArtifact`, `PythonArtifact`, `ChartArtifact`, `ReferenceArtifact`, and discriminated union `Artifact`.
- Consumes: `BaseModel`, `ConfigDict`, `Field`, `field_validator`, and `model_validator` from `dbgpt._private.pydantic`.

- [ ] **Step 1: Write failing artifact behavior tests**

Create `test_report.py` with literal fixtures that map current DB-GPT outputs:

```python
from datetime import datetime

import pytest
from pydantic import ValidationError

from dbgpt.core.interface.report import (
    ArtifactContent,
    ChartArtifact,
    ExecutionReference,
    PythonArtifact,
    ReferenceArtifact,
    SQLArtifact,
)


EXECUTION = ExecutionReference(
    conv_uid="conv-1",
    step_id="step-1",
    message_id="message-1",
    task_id="task-1",
    run_id="run-1",
    action="Chart",
)
CREATED_AT = datetime(2026, 8, 21, 9, 30, 0)


def test_sql_artifact_preserves_existing_chart_action_output() -> None:
    artifact = SQLArtifact(
        artifact_id="sql-sales",
        execution=EXECUTION,
        created_at=CREATED_AT,
        sql="SELECT day, gmv FROM sales",
        database="commerce",
        display_type="response_table",
        columns=["day", "gmv"],
        row_count=1,
        result=ArtifactContent(
            inline=[{"day": "2026-08-20", "gmv": 1186000}]
        ),
    )

    assert artifact.type == "sql"
    assert artifact.result.inline == [
        {"day": "2026-08-20", "gmv": 1186000}
    ]
    assert artifact.execution.step_id == "step-1"


def test_python_and_chart_artifacts_preserve_real_dependency() -> None:
    python = PythonArtifact(
        artifact_id="python-delta",
        execution=EXECUTION,
        input_artifact_ids=["sql-sales"],
        created_at=CREATED_AT,
        code="delta = current - previous",
        exit_code=0,
        stdout="delta=-120000",
        result=ArtifactContent(inline={"delta": -120000}),
    )
    chart = ChartArtifact(
        artifact_id="chart-sales",
        execution=EXECUTION,
        input_artifact_ids=["python-delta"],
        created_at=CREATED_AT,
        chart_type="line",
        spec={"x": "day", "y": "gmv"},
        output=ArtifactContent(uri="dbgpt-fs://local/reports/chart-sales"),
    )

    assert python.language == "python"
    assert python.input_artifact_ids == ["sql-sales"]
    assert chart.input_artifact_ids == ["python-delta"]


def test_reference_artifact_preserves_current_knowledge_citation() -> None:
    artifact = ReferenceArtifact(
        artifact_id="knowledge-revenue",
        execution=EXECUTION,
        created_at=CREATED_AT,
        reference_type="knowledge",
        source_id="chunk-1",
        source_name="annual-report.md",
        uri="https://example.test/annual-report",
        locator={"chunkIndex": 1, "path": "reports/annual-report.md"},
        excerpt="Revenue grew by 20 percent.",
        score=0.91,
    )

    assert artifact.source_id == "chunk-1"
    assert artifact.locator["chunkIndex"] == 1
    assert artifact.score == 0.91


def test_artifact_content_requires_inline_or_uri() -> None:
    with pytest.raises(ValidationError, match="inline or uri"):
        ArtifactContent()


@pytest.mark.parametrize(
    ("model", "kwargs"),
    [
        (ExecutionReference, {"conv_uid": " ", "step_id": "step-1"}),
        (ArtifactContent, {"uri": " "}),
        (
            SQLArtifact,
            {
                "artifact_id": " ",
                "execution": EXECUTION,
                "created_at": CREATED_AT,
                "sql": "SELECT 1",
                "result": {"inline": [{"value": 1}]},
            },
        ),
    ],
)
def test_protocol_rejects_blank_required_text(model, kwargs) -> None:
    with pytest.raises(ValidationError, match="must not be blank"):
        model(**kwargs)


def test_protocol_rejects_undeclared_fields() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ExecutionReference(conv_uid="conv-1", step_id="step-1", unknown=True)
```

Each test catches a concrete regression: dropped action output data, lost dependency IDs, lost citation fields, empty persisted content, blank stable IDs, or silently accepted wire fields.

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
conda run -n dbgpt_env pytest packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'dbgpt.core.interface.report'`. This is the correct RED because the public protocol module does not exist.

- [ ] **Step 3: Implement the minimal artifact protocol**

Create `report.py` with a strict base model, reusable blank validation, artifact content validation, four artifact variants, and the union:

```python
"""Core report and evidence protocol."""

from datetime import datetime
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from dbgpt._private.pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class _ProtocolModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _require_non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


class ExecutionReference(_ProtocolModel):
    conv_uid: str
    step_id: str
    message_id: Optional[str] = None
    task_id: Optional[str] = None
    run_id: Optional[str] = None
    action: Optional[str] = None

    _validate_conv_uid = field_validator("conv_uid")(_require_non_blank)
    _validate_step_id = field_validator("step_id")(_require_non_blank)


class ArtifactContent(_ProtocolModel):
    inline: Optional[Any] = None
    uri: Optional[str] = None
    media_type: Optional[str] = None
    sha256: Optional[str] = None
    size: Optional[int] = Field(default=None, ge=0)

    @field_validator("uri")
    @classmethod
    def validate_uri(cls, value: Optional[str]) -> Optional[str]:
        return _require_non_blank(value) if value is not None else value

    @model_validator(mode="after")
    def validate_location(self) -> "ArtifactContent":
        if self.inline is None and self.uri is None:
            raise ValueError("artifact content requires inline or uri")
        return self


class BaseArtifact(_ProtocolModel):
    artifact_id: str
    name: Optional[str] = None
    execution: ExecutionReference
    input_artifact_ids: List[str] = Field(default_factory=list)
    created_at: datetime
    metadata: Dict[str, Any] = Field(default_factory=dict)

    _validate_artifact_id = field_validator("artifact_id")(_require_non_blank)


class SQLArtifact(BaseArtifact):
    type: Literal["sql"] = "sql"
    sql: str
    database: Optional[str] = None
    display_type: Optional[str] = None
    columns: List[str] = Field(default_factory=list)
    row_count: Optional[int] = Field(default=None, ge=0)
    result: ArtifactContent

    _validate_sql = field_validator("sql")(_require_non_blank)


class PythonArtifact(BaseArtifact):
    type: Literal["python"] = "python"
    code: str
    language: str = "python"
    exit_code: int
    stdout: Optional[str] = None
    result: Optional[ArtifactContent] = None

    _validate_code = field_validator("code")(_require_non_blank)
    _validate_language = field_validator("language")(_require_non_blank)


class ChartArtifact(BaseArtifact):
    type: Literal["chart"] = "chart"
    chart_type: str
    spec: Dict[str, Any] = Field(default_factory=dict)
    output: ArtifactContent

    _validate_chart_type = field_validator("chart_type")(_require_non_blank)


class ReferenceArtifact(BaseArtifact):
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
```

Use explicit validators only for current required text. Do not add URI reachability, score upper bounds, or hash format checks that are absent from the specification.

- [ ] **Step 4: Run focused tests and Ruff; verify GREEN**

Run:

```powershell
conda run -n dbgpt_env pytest packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report.py -q
conda run -n dbgpt_env ruff check packages/dbgpt-core/src/dbgpt/core/interface/report.py packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report.py
conda run -n dbgpt_env ruff format --check packages/dbgpt-core/src/dbgpt/core/interface/report.py packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report.py
```

Expected: all artifact tests pass and both Ruff commands exit 0.

- [ ] **Step 5: Commit the artifact contract**

Run the Step 4 commands again immediately before committing, then:

```powershell
git add packages/dbgpt-core/src/dbgpt/core/interface/report.py packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report.py
git commit --no-verify -m "feat(core): add report artifact protocol"
```

Expected: one commit containing only the artifact protocol and its tests.

### Task 2: Report sections, typed blocks, and serialization

**Files:**
- Modify: `packages/dbgpt-core/src/dbgpt/core/interface/report.py`
- Modify: `packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report.py`

**Interfaces:**
- Consumes: `Artifact`, `ArtifactContent`, and concrete artifact classes from Task 1.
- Produces: `EvidenceReference`, `MetricBlock`, `TableBlock`, `ChartBlock`, `InsightBlock`, `TextBlock`, `ReportBlock`, `ReportSection`, and `ReportDocument`.

- [ ] **Step 1: Add failing report construction and round-trip tests**

Append a literal report fixture and behavior tests:

```python
from dbgpt.core.interface.report import (
    ChartBlock,
    EvidenceReference,
    InsightBlock,
    MetricBlock,
    ReportDocument,
    ReportSection,
    TableBlock,
    TextBlock,
)


def _valid_report_payload() -> dict:
    return {
        "schema_version": 1,
        "report_id": "report-sales",
        "title": "Shanghai GMV analysis",
        "conv_uid": "conv-1",
        "created_at": "2026-08-21T09:30:00",
        "artifacts": [
            {
                "type": "sql",
                "artifact_id": "sql-sales",
                "execution": {"conv_uid": "conv-1", "step_id": "step-1"},
                "created_at": "2026-08-21T09:30:00",
                "sql": "SELECT day, gmv FROM sales",
                "columns": ["day", "gmv"],
                "row_count": 1,
                "result": {
                    "inline": [{"day": "2026-08-20", "gmv": 1186000}]
                },
            },
            {
                "type": "python",
                "artifact_id": "python-delta",
                "execution": {"conv_uid": "conv-1", "step_id": "step-2"},
                "input_artifact_ids": ["sql-sales"],
                "created_at": "2026-08-21T09:31:00",
                "code": "delta = current - previous",
                "exit_code": 0,
                "result": {"inline": {"delta": -120000}},
            },
            {
                "type": "chart",
                "artifact_id": "chart-sales",
                "execution": {"conv_uid": "conv-1", "step_id": "step-3"},
                "input_artifact_ids": ["python-delta"],
                "created_at": "2026-08-21T09:32:00",
                "chart_type": "line",
                "spec": {"x": "day", "y": "gmv"},
                "output": {"uri": "dbgpt-fs://local/reports/chart-sales"},
            },
        ],
        "sections": [
            {
                "section_id": "summary",
                "title": "Summary",
                "blocks": [
                    {
                        "type": "metric",
                        "block_id": "metric-gmv",
                        "title": "GMV",
                        "value": 1186000,
                        "display_value": "1.186M",
                        "unit": "CNY",
                        "evidence": [
                            {
                                "artifact_id": "sql-sales",
                                "role": "source",
                                "selector": {"column": "gmv", "row": 0},
                                "target_selector": {"marker_id": "gmv-value"},
                            }
                        ],
                    },
                    {
                        "type": "table",
                        "block_id": "table-gmv",
                        "title": "GMV rows",
                        "data_artifact_id": "sql-sales",
                    },
                    {
                        "type": "chart",
                        "block_id": "chart-gmv",
                        "title": "GMV trend",
                        "chart_artifact_id": "chart-sales",
                    },
                    {
                        "type": "insight",
                        "block_id": "insight-delta",
                        "content": "GMV decreased by 120000 CNY.",
                        "evidence": [
                            {
                                "artifact_id": "python-delta",
                                "role": "calculation",
                            }
                        ],
                    },
                    {
                        "type": "text",
                        "block_id": "text-method",
                        "content": "The report uses executed data-assistant steps.",
                    },
                ],
            }
        ],
    }


def test_report_round_trip_preserves_concrete_artifacts_and_blocks() -> None:
    report = ReportDocument.model_validate(_valid_report_payload())
    restored = ReportDocument.model_validate_json(report.model_dump_json())

    assert isinstance(restored.artifacts[0], SQLArtifact)
    assert isinstance(restored.artifacts[1], PythonArtifact)
    assert isinstance(restored.artifacts[2], ChartArtifact)
    assert isinstance(restored.sections[0].blocks[0], MetricBlock)
    assert isinstance(restored.sections[0].blocks[1], TableBlock)
    assert isinstance(restored.sections[0].blocks[2], ChartBlock)
    assert isinstance(restored.sections[0].blocks[3], InsightBlock)
    assert isinstance(restored.sections[0].blocks[4], TextBlock)
    assert restored.sections[0].blocks[0].evidence[0].selector == {
        "column": "gmv",
        "row": 0,
    }


def test_report_json_schema_exposes_type_discriminators() -> None:
    schema = ReportDocument.model_json_schema()

    artifact_items = schema["properties"]["artifacts"]["items"]
    block_items = schema["$defs"]["ReportSection"]["properties"]["blocks"][
        "items"
    ]
    assert artifact_items["discriminator"]["propertyName"] == "type"
    assert block_items["discriminator"]["propertyName"] == "type"


@pytest.mark.parametrize("block_type", ["metric", "insight"])
def test_derived_blocks_require_evidence(block_type: str) -> None:
    payload = _valid_report_payload()["sections"][0]["blocks"]
    block = next(item for item in payload if item["type"] == block_type)
    block["evidence"] = []
    model = MetricBlock if block_type == "metric" else InsightBlock

    with pytest.raises(ValidationError, match="requires evidence"):
        model.model_validate(block)
```

The round-trip test catches removed discriminators or widened untyped dicts. The evidence test catches derived blocks becoming unverifiable.

- [ ] **Step 2: Run new tests and verify RED**

Run:

```powershell
conda run -n dbgpt_env pytest packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report.py -q
```

Expected: collection fails because `ReportDocument` and report block symbols are not yet defined.

- [ ] **Step 3: Implement the minimal report models**

Append these models to `report.py`:

```python
class EvidenceReference(_ProtocolModel):
    artifact_id: str
    role: Literal["source", "calculation", "support"]
    selector: Dict[str, Any] = Field(default_factory=dict)
    target_selector: Dict[str, Any] = Field(default_factory=dict)

    _validate_artifact_id = field_validator("artifact_id")(_require_non_blank)


class BaseReportBlock(_ProtocolModel):
    block_id: str
    title: Optional[str] = None
    evidence: List[EvidenceReference] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    _validate_block_id = field_validator("block_id")(_require_non_blank)


class MetricBlock(BaseReportBlock):
    type: Literal["metric"] = "metric"
    value: Any
    display_value: Optional[str] = None
    unit: Optional[str] = None

    @model_validator(mode="after")
    def validate_evidence(self) -> "MetricBlock":
        if not self.evidence:
            raise ValueError("metric block requires evidence")
        return self


class ChartBlock(BaseReportBlock):
    type: Literal["chart"] = "chart"
    chart_artifact_id: str
    description: Optional[str] = None

    _validate_chart_artifact_id = field_validator("chart_artifact_id")(
        _require_non_blank
    )


class TableBlock(BaseReportBlock):
    type: Literal["table"] = "table"
    data_artifact_id: str
    description: Optional[str] = None

    _validate_data_artifact_id = field_validator("data_artifact_id")(
        _require_non_blank
    )


class InsightBlock(BaseReportBlock):
    type: Literal["insight"] = "insight"
    content: str

    _validate_content = field_validator("content")(_require_non_blank)

    @model_validator(mode="after")
    def validate_evidence(self) -> "InsightBlock":
        if not self.evidence:
            raise ValueError("insight block requires evidence")
        return self


class TextBlock(BaseReportBlock):
    type: Literal["text"] = "text"
    content: str

    _validate_content = field_validator("content")(_require_non_blank)


ReportBlock = Annotated[
    Union[MetricBlock, TableBlock, ChartBlock, InsightBlock, TextBlock],
    Field(discriminator="type"),
]


class ReportSection(_ProtocolModel):
    section_id: str
    title: str
    blocks: List[ReportBlock] = Field(default_factory=list)

    _validate_section_id = field_validator("section_id")(_require_non_blank)
    _validate_title = field_validator("title")(_require_non_blank)


class ReportDocument(_ProtocolModel):
    schema_version: Literal[1] = 1
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
```

Do not add report-wide graph validation in this task; Task 3 must first prove each failure mode with RED tests.

- [ ] **Step 4: Run tests and Ruff; verify GREEN**

Run the same focused pytest and Ruff commands from Task 1. Expected: all tests pass and both Ruff checks exit 0.

- [ ] **Step 5: Commit the typed report document**

After repeating Step 4 immediately before the commit:

```powershell
git add packages/dbgpt-core/src/dbgpt/core/interface/report.py packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report.py
git commit --no-verify -m "feat(core): add typed report document"
```

### Task 3: Report-wide reference and dependency validation

**Files:**
- Modify: `packages/dbgpt-core/src/dbgpt/core/interface/report.py`
- Modify: `packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report.py`

**Interfaces:**
- Consumes: `ReportDocument`, concrete artifact classes, sections, and blocks from Tasks 1 and 2.
- Produces: `ReportDocument.validate_integrity()` model validation covering uniqueness, references, chart typing, and acyclic data provenance.

- [ ] **Step 1: Add failing uniqueness and dangling-reference tests**

Append tests that mutate the literal fixture independently:

```python
@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda data: data["artifacts"].append(data["artifacts"][0].copy()),
            "duplicate artifact_id",
        ),
        (
            lambda data: data["sections"].append(data["sections"][0].copy()),
            "duplicate section_id",
        ),
        (
            lambda data: data["sections"][0]["blocks"].append(
                data["sections"][0]["blocks"][0].copy()
            ),
            "duplicate block_id",
        ),
    ],
)
def test_report_rejects_duplicate_ids(mutation, message: str) -> None:
    payload = _valid_report_payload()
    mutation(payload)

    with pytest.raises(ValidationError, match=message):
        ReportDocument.model_validate(payload)


def test_report_rejects_dangling_artifact_dependency() -> None:
    payload = _valid_report_payload()
    payload["artifacts"][1]["input_artifact_ids"] = ["missing-sql"]

    with pytest.raises(ValidationError, match="unknown input artifact"):
        ReportDocument.model_validate(payload)


def test_report_rejects_dangling_evidence() -> None:
    payload = _valid_report_payload()
    payload["sections"][0]["blocks"][0]["evidence"][0][
        "artifact_id"
    ] = "missing-sql"

    with pytest.raises(ValidationError, match="unknown evidence artifact"):
        ReportDocument.model_validate(payload)


def test_chart_block_requires_chart_artifact() -> None:
    payload = _valid_report_payload()
    payload["sections"][0]["blocks"][1]["chart_artifact_id"] = "sql-sales"

    with pytest.raises(ValidationError, match="must reference a chart artifact"):
        ReportDocument.model_validate(payload)
```

- [ ] **Step 2: Run uniqueness/reference tests and verify RED**

Run:

```powershell
conda run -n dbgpt_env pytest packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report.py -q
```

Expected: the new tests fail because Task 2 accepts duplicate and dangling IDs.

- [ ] **Step 3: Implement uniqueness and reference validation**

Add `_find_duplicate` before `ReportDocument`, then insert the shown
`validate_integrity` method at the end of the `ReportDocument` class:

```python
def _find_duplicate(values: List[str]) -> Optional[str]:
    seen = set()
    for value in values:
        if value in seen:
            return value
        seen.add(value)
    return None

    @model_validator(mode="after")
    def validate_integrity(self) -> "ReportDocument":
        artifacts = {
            artifact.artifact_id: artifact for artifact in self.artifacts
        }
        duplicate = _find_duplicate(
            [artifact.artifact_id for artifact in self.artifacts]
        )
        if duplicate:
            raise ValueError(f"duplicate artifact_id: {duplicate}")

        duplicate = _find_duplicate(
            [section.section_id for section in self.sections]
        )
        if duplicate:
            raise ValueError(f"duplicate section_id: {duplicate}")

        blocks = [block for section in self.sections for block in section.blocks]
        duplicate = _find_duplicate([block.block_id for block in blocks])
        if duplicate:
            raise ValueError(f"duplicate block_id: {duplicate}")

        for artifact in self.artifacts:
            for input_id in artifact.input_artifact_ids:
                if input_id not in artifacts:
                    raise ValueError(f"unknown input artifact: {input_id}")

        for block in blocks:
            for evidence in block.evidence:
                if evidence.artifact_id not in artifacts:
                    raise ValueError(
                        f"unknown evidence artifact: {evidence.artifact_id}"
                    )
            if isinstance(block, ChartBlock):
                chart = artifacts.get(block.chart_artifact_id)
                if not isinstance(chart, ChartArtifact):
                    raise ValueError(
                        "chart_artifact_id must reference a chart artifact"
                    )

        return self
```

Do not add dependency graph validation in this RED-GREEN cycle. Its behavior is
introduced only after the dedicated failing tests in the next step.

- [ ] **Step 4: Verify uniqueness/reference GREEN**

Run only the new tests first:

```powershell
conda run -n dbgpt_env pytest packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report.py -q -k "duplicate or dangling or chart_block"
```

Expected: selected tests pass.

- [ ] **Step 5: Add failing dependency-cycle and chart-lineage tests**

Append:

```python
@pytest.mark.parametrize(
    "dependencies",
    [
        {"python-delta": ["python-delta"]},
        {"sql-sales": ["chart-sales"], "chart-sales": ["sql-sales"]},
    ],
)
def test_report_rejects_artifact_dependency_cycles(dependencies: dict) -> None:
    payload = _valid_report_payload()
    artifacts = {item["artifact_id"]: item for item in payload["artifacts"]}
    for artifact_id, input_ids in dependencies.items():
        artifacts[artifact_id]["input_artifact_ids"] = input_ids

    with pytest.raises(ValidationError, match="artifact dependency cycle"):
        ReportDocument.model_validate(payload)


def test_chart_artifact_requires_sql_or_python_lineage() -> None:
    payload = _valid_report_payload()
    payload["artifacts"].append(
        {
            "type": "reference",
            "artifact_id": "knowledge-only",
            "execution": {"conv_uid": "conv-1", "step_id": "step-4"},
            "created_at": "2026-08-21T09:33:00",
            "reference_type": "knowledge",
            "source_id": "chunk-1",
        }
    )
    payload["artifacts"][2]["input_artifact_ids"] = ["knowledge-only"]

    with pytest.raises(ValidationError, match="SQL or Python lineage"):
        ReportDocument.model_validate(payload)
```

These tests catch both DFS-stack mistakes and a chart that is visually present but not based on executed analysis data.

- [ ] **Step 6: Run graph tests and verify RED**

Run:

```powershell
conda run -n dbgpt_env pytest packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report.py -q -k "cycle or lineage"
```

Expected: the new tests fail because `_validate_artifact_graph` does not yet enforce topology.

- [ ] **Step 7: Implement cycle and lineage validation**

Insert `self._validate_artifact_graph(artifacts)` immediately before the
existing `return self` in `validate_integrity`, then implement the private
method on `ReportDocument`:

```python
def _validate_artifact_graph(self, artifacts: Dict[str, Artifact]) -> None:
    visiting = set()
    visited = set()

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

    def has_analysis_lineage(artifact_id: str) -> bool:
        artifact = artifacts[artifact_id]
        if isinstance(artifact, (SQLArtifact, PythonArtifact)):
            return True
        return any(
            has_analysis_lineage(input_id)
            for input_id in artifact.input_artifact_ids
        )

    for artifact in self.artifacts:
        if isinstance(artifact, ChartArtifact):
            if not artifact.input_artifact_ids or not any(
                has_analysis_lineage(input_id)
                for input_id in artifact.input_artifact_ids
            ):
                raise ValueError(
                    f"chart artifact {artifact.artifact_id} requires "
                    "SQL or Python lineage"
                )
```

The prior cycle check guarantees recursive lineage traversal terminates.

- [ ] **Step 8: Run the complete focused suite and Ruff; verify GREEN**

Run:

```powershell
conda run -n dbgpt_env pytest packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report.py -q
conda run -n dbgpt_env ruff check packages/dbgpt-core/src/dbgpt/core/interface/report.py packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report.py
conda run -n dbgpt_env ruff format --check packages/dbgpt-core/src/dbgpt/core/interface/report.py packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report.py
```

Expected: all protocol tests pass and Ruff exits 0.

- [ ] **Step 9: Commit integrity validation**

Repeat Step 8 immediately before committing, then:

```powershell
git add packages/dbgpt-core/src/dbgpt/core/interface/report.py packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report.py
git commit --no-verify -m "feat(core): validate report evidence graph"
```

### Task 4: Public exports, type checking, and PR verification

**Files:**
- Modify: `packages/dbgpt-core/src/dbgpt/core/__init__.py`
- Modify: `packages/dbgpt-core/src/dbgpt/core/interface/report.py`
- Modify: `packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report.py`

**Interfaces:**
- Consumes: all protocol types from Tasks 1-3.
- Produces: stable imports from `dbgpt.core` and an explicit `__all__` in the protocol module.

- [ ] **Step 1: Add a failing public-import test**

Append:

```python
def test_report_protocol_is_available_from_core_public_api() -> None:
    import dbgpt.core as core

    public_names = {
        "Artifact",
        "ArtifactContent",
        "ChartArtifact",
        "ChartBlock",
        "EvidenceReference",
        "ExecutionReference",
        "InsightBlock",
        "MetricBlock",
        "PythonArtifact",
        "ReferenceArtifact",
        "ReportBlock",
        "ReportDocument",
        "ReportSection",
        "SQLArtifact",
        "TableBlock",
        "TextBlock",
    }

    assert all(hasattr(core, name) for name in public_names)
    assert core.ReportDocument is ReportDocument
    assert core.SQLArtifact is SQLArtifact
```

The test catches an implementation that works only through a private module path and cannot be consumed consistently by App or Serve.

- [ ] **Step 2: Run the import test and verify RED**

Run:

```powershell
conda run -n dbgpt_env pytest packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report.py -q -k public_api
```

Expected: import fails because `dbgpt.core` does not export the protocol.

- [ ] **Step 3: Add explicit module and Core exports**

Add to `report.py`:

```python
__all__ = [
    "Artifact",
    "ArtifactContent",
    "ChartArtifact",
    "ChartBlock",
    "EvidenceReference",
    "ExecutionReference",
    "InsightBlock",
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
```

Import the same names in `dbgpt/core/__init__.py` from
`dbgpt.core.interface.report`, then append the same strings to the existing
`__ALL__` list. Do not export private base models or validation helpers.

- [ ] **Step 4: Run public import and focused tests; verify GREEN**

Run:

```powershell
conda run -n dbgpt_env pytest packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report.py -q
```

Expected: all protocol tests pass.

- [ ] **Step 5: Format files, then run complete fresh verification**

Run formatting only on PR1 files:

```powershell
conda run -n dbgpt_env ruff format packages/dbgpt-core/src/dbgpt/core/interface/report.py packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report.py packages/dbgpt-core/src/dbgpt/core/__init__.py
conda run -n dbgpt_env ruff check --fix --select I packages/dbgpt-core/src/dbgpt/core/interface/report.py packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report.py packages/dbgpt-core/src/dbgpt/core/__init__.py
```

Then verify from a clean command invocation:

```powershell
conda run -n dbgpt_env pytest packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report.py -q
conda run -n dbgpt_env pytest packages/dbgpt-core/src/dbgpt/core/interface/tests -q
conda run -n dbgpt_env ruff check packages/dbgpt-core/src/dbgpt/core/interface/report.py packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report.py packages/dbgpt-core/src/dbgpt/core/__init__.py
conda run -n dbgpt_env ruff format --check packages/dbgpt-core/src/dbgpt/core/interface/report.py packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report.py packages/dbgpt-core/src/dbgpt/core/__init__.py
conda run -n dbgpt_env mypy --config-file .mypy.ini --ignore-missing-imports packages/dbgpt-core/src/dbgpt/core/interface/report.py
git diff --check
git status --short
```

Expected:

- protocol tests and existing Core interface tests report zero failures;
- Ruff and mypy exit 0;
- `git diff --check` emits no output;
- Git status lists only the intended PR1 source and test changes plus the already committed design/plan history.

- [ ] **Step 6: Review the diff against every specification requirement**

Run:

```powershell
git diff -- packages/dbgpt-core/src/dbgpt/core/interface/report.py packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report.py packages/dbgpt-core/src/dbgpt/core/__init__.py
git diff upstream/main...HEAD --stat
```

Confirm manually that the diff contains no App, Serve, web, persistence,
refresh, export, action, SSE, lockfile, or legacy report changes.

- [ ] **Step 7: Commit public exports and final verification state**

After repeating Step 5's verification commands immediately before commit:

```powershell
git add packages/dbgpt-core/src/dbgpt/core/interface/report.py packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report.py packages/dbgpt-core/src/dbgpt/core/__init__.py
git commit --no-verify -m "feat(core): expose report evidence protocol"
```

- [ ] **Step 8: Verify the committed branch**

Run the complete Step 5 verification again after the commit, followed by:

```powershell
git status --short --branch
git log --oneline --decorate -6
```

Expected: verification remains green, the worktree is clean, and the branch
contains the design, plan, and focused PR1 implementation commits only.
