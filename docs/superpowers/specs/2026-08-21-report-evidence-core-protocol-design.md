# Report and Evidence Core Protocol Design

## Purpose

DB-GPT can already execute SQL, run Python, render charts, retrieve knowledge,
and generate report text. Those results currently travel through different
structures such as `ActionOutput`, data-assistant SSE step events, persisted
tool output paths, and the dashboard-specific `ReportData`. There is no shared
contract that binds a report value, chart, or insight to the execution output
that produced it.

PR1 introduces that shared contract. It defines a versioned, serializable
Report and Evidence protocol in `dbgpt-core`. It does not capture, persist,
refresh, expose, or render reports. Later PRs can adapt existing execution
outputs to this protocol without making `dbgpt-core` depend on `dbgpt-app` or
`dbgpt-serve`.

## Scope

PR1 includes:

- a report document made of sections and typed blocks;
- five report block variants: metric, table, chart, insight, and text;
- typed SQL, Python, chart, and reference artifacts;
- references from report blocks to artifacts;
- artifact-to-artifact dependencies;
- execution references that use DB-GPT's existing conversation and step IDs;
- strict structural validation and JSON round-trip support;
- public exports and focused unit tests.

The evidence graph describes an execution lineage, not a model reasoning or
chain-of-thought trace. Its intended direction is
`source artifact -> executed artifact -> rendered artifact -> report block`.
Producer adapters may persist tool/action inputs, output summaries, status,
and timing in later PRs, but the protocol does not expose private reasoning.

PR1 excludes:

- changes to `ActionOutput`, `ChartAction`, `CodeAction`, or SSE events;
- adapters that capture real executions;
- persistence models, DAOs, migrations, or APIs;
- refresh scheduling or dependency execution;
- Markdown, HTML, PDF, or Word export;
- frontend components;
- migration of the dashboard `ReportData` model or the earlier frontend Demo.

## Placement and Dependency Direction

The protocol will live at:

```text
packages/dbgpt-core/src/dbgpt/core/interface/report.py
```

This follows the existing placement of cross-layer domain contracts in
`dbgpt.core.interface`, including messages, files, knowledge documents, and
storage interfaces. Both `dbgpt-app` and `dbgpt-serve` already depend on
`dbgpt-core`; placing the protocol in either higher-level package would create
an invalid reverse dependency.

The models will use `dbgpt._private.pydantic` and the project's existing
Pydantic 2 dependency. No new dependency or lockfile change is required. The
public types will also be exported from `dbgpt.core` in the same style as the
other core interfaces.

The existing dashboard model at
`dbgpt_app.scene.chat_dashboard.data_preparation.report_schma` remains
unchanged. It is scene-specific and cannot serve as a cross-layer protocol.

## Existing DB-GPT Data Mapping

Every protocol field has a source in the current implementation:

| Protocol data | Existing source |
|---|---|
| `conv_uid` | Conversation models and dashboard `ReportData` |
| `step_id` | `step.start`, `step.chunk`, and `step.done` events in `agentic_data_api.py` |
| `message_id` | Core message identifiers |
| `task_id`, `run_id` | Scheduled task and chat replay models |
| SQL and display type | `SqlInput` in `ChartAction` |
| Database name | `ActionOutput.resource_value` emitted by `ChartAction` |
| SQL columns, rows, and count | Serialized `ChartAction` content |
| Python code, logs, and status | `CodeAction` execution result |
| Persisted large output | `ActionOutput.persisted_path` and `FileStorageURI` |
| Knowledge citation | Existing citation ID, source name, chunk index, score, path, and URL |
| Artifact dependency | The real SQL to Python to chart execution order |

The protocol does not require producers to start emitting these structures in
PR1. This mapping defines how later adapters will populate them.

## Core Models

All protocol models reject undeclared top-level fields with Pydantic
`extra="forbid"`. Extensible producer-specific values must be placed in an
explicit `metadata`, `locator`, or `spec` mapping.

### ExecutionReference

`ExecutionReference` identifies the real data-assistant execution that
produced an artifact:

```python
class ExecutionReference(BaseModel):
    conv_uid: str
    step_id: str
    message_id: Optional[str] = None
    task_id: Optional[str] = None
    run_id: Optional[str] = None
    action: Optional[str] = None
```

`conv_uid` and `step_id` are required because they are the stable location
available in the current interactive data-assistant flow. `task_id` and
`run_id` carry the existing scheduled replay identity when applicable.

### ArtifactContent

`ArtifactContent` represents either an inline result or a reference to a
persisted result:

```python
class ArtifactContent(BaseModel):
    inline: Optional[Any] = None
    uri: Optional[str] = None
    media_type: Optional[str] = None
    sha256: Optional[str] = None
    size: Optional[int] = None
```

At least one of `inline` and `uri` must be present. Small SQL results can use
`inline`; larger results can use the existing persisted path or a
`FileStorageURI`. `sha256` is optional because PR1 does not compute hashes,
but it provides a stable location for later integrity and refresh checks.

### Artifacts

Every artifact has these common fields:

```python
artifact_id: str
type: discriminator
name: Optional[str]
execution: ExecutionReference
input_artifact_ids: List[str]
created_at: datetime
metadata: Dict[str, Any]
```

Artifact IDs are supplied by the execution adapter. The protocol does not
generate random IDs because persisted reports and replayed executions need
stable references.

The supported artifact variants are:

```python
class SQLArtifact(BaseArtifact):
    type: Literal["sql"]
    sql: str
    database: Optional[str]
    display_type: Optional[str]
    columns: List[str]
    row_count: Optional[int]
    result: ArtifactContent

class PythonArtifact(BaseArtifact):
    type: Literal["python"]
    code: str
    language: str = "python"
    exit_code: int
    stdout: Optional[str]
    result: Optional[ArtifactContent]

class ChartArtifact(BaseArtifact):
    type: Literal["chart"]
    chart_type: str
    spec: Dict[str, Any]
    output: ArtifactContent

class ReferenceArtifact(BaseArtifact):
    type: Literal["reference"]
    reference_type: Literal["knowledge", "memory", "web", "model"]
    source_id: str
    source_name: Optional[str]
    uri: Optional[str]
    locator: Dict[str, Any]
    excerpt: Optional[str]
    score: Optional[float]
```

`ReferenceArtifact` deliberately remains generic. The project requirement
prioritizes SQL, Python, and chart evidence, while current knowledge,
memory, web, and model sources do not yet share one stable specialized
contract. Their existing identifiers and citation values fit the generic
reference without locking later integrations into invented fields.

The artifact union is discriminated by `type`, so Pydantic validation and
JSON Schema preserve the concrete artifact type.

## Evidence Binding

`EvidenceReference` binds a report block to a real artifact:

```python
class EvidenceReference(BaseModel):
    artifact_id: str
    role: Literal["source", "calculation", "support"]
    selector: Dict[str, Any]
    target_selector: Dict[str, Any]
```

The roles have narrow meanings:

- `source`: the artifact directly supplies displayed data;
- `calculation`: the artifact computes or transforms the displayed result;
- `support`: the artifact supports a narrative conclusion.

`selector` preserves producer-specific location information such as a SQL
column, row, cell, or Python output key. PR1 stores selectors but does not
interpret them. `target_selector` locates the claim inside the report block,
for example an HTML/Markdown marker ID for one of several metrics embedded in
the same text block. Keeping source and target selectors separate allows the
frontend to open exactly the evidence associated with the clicked value.

## Report Document

The report document is a sequence of sections. Each section contains a
discriminated union of report blocks.

Common block fields are:

```python
block_id: str
type: discriminator
title: Optional[str]
evidence: List[EvidenceReference]
metadata: Dict[str, Any]
```

The variants are:

```python
class MetricBlock(BaseReportBlock):
    type: Literal["metric"]
    value: Any
    display_value: Optional[str]
    unit: Optional[str]

class ChartBlock(BaseReportBlock):
    type: Literal["chart"]
    chart_artifact_id: str
    description: Optional[str]

class TableBlock(BaseReportBlock):
    type: Literal["table"]
    data_artifact_id: str
    description: Optional[str]

class InsightBlock(BaseReportBlock):
    type: Literal["insight"]
    content: str

class TextBlock(BaseReportBlock):
    type: Literal["text"]
    content: str
```

`ReportSection` contains `section_id`, `title`, and `blocks`.
`ReportDocument` contains:

```python
schema_version: Literal[1] = 1
report_id: str
title: str
conv_uid: str
created_at: datetime
sections: List[ReportSection]
artifacts: List[Artifact]
metadata: Dict[str, Any]
```

Metric and insight blocks require evidence. Text blocks may omit evidence
because narrative headings and explanatory copy are not necessarily derived
from an execution. Chart blocks use `chart_artifact_id` as the render target
and may carry additional evidence for upstream SQL or Python artifacts. Table
blocks use `data_artifact_id` to reference a SQL or Python artifact containing
the tabular result; they do not duplicate result rows in the report schema.

## Validation Rules

Pydantic field and model validators enforce protocol integrity:

1. Required identifiers and required text fields cannot be blank.
2. Artifact IDs are unique within a report.
3. Section IDs are unique within a report.
4. Block IDs are unique across all report sections.
5. Every `EvidenceReference.artifact_id` exists in the report artifact set.
6. Every `input_artifact_ids` entry exists in the report artifact set.
7. An artifact cannot directly or indirectly depend on itself.
8. A `ChartBlock.chart_artifact_id` exists and identifies a `ChartArtifact`.
9. A `ChartArtifact` has at least one input artifact and ultimately depends on
   SQL or Python data.
10. A `TableBlock.data_artifact_id` exists and identifies a SQL or Python
    artifact.
11. `ArtifactContent` contains inline data, a URI, or both.
12. `size`, `row_count`, and reference `score` cannot be negative.
13. Metric and insight blocks contain at least one evidence reference.

Cross-reference and dependency validation occurs at `ReportDocument`, where
the complete artifact registry is available. Validators raise the project's
existing Pydantic `ValidationError`; PR1 does not introduce a parallel
exception hierarchy.

Validation is structural. It does not execute SQL, access a URI, recompute a
hash, judge the truth of an insight, or verify that a selector points to an
existing cell. Those responsibilities belong to later execution and storage
integrations.

## Serialization and Compatibility

- `schema_version` is fixed to integer `1` for the initial protocol.
- Model dump, JSON dump, and model validation use the existing Pydantic
  behavior supplied by `dbgpt._private.pydantic`.
- Datetimes follow DB-GPT's current datetime serialization behavior; PR1 does
  not impose a new project-wide timezone convention.
- The Artifact and Block unions use the `type` discriminator in generated
  JSON Schema.
- Old `ReportData`, serialized action reports, and existing SSE events remain
  valid because PR1 does not change them.
- Future optional producer information belongs in explicit metadata fields.
  Breaking protocol changes require a new schema version.

PR3 subsequently applies that rule by introducing schema version 2 for real
execution outcomes. Version 1 keeps its original serialized artifact field set;
version 2 adds required artifact status and duration plus failure details.

## Testing

Focused tests will live at:

```text
packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report.py
```

Tests will cover:

- constructing a complete SQL to Python to chart report;
- model dump and JSON round trips without losing concrete union types;
- discriminators in generated JSON Schema;
- mapping current knowledge citation values into a reference artifact;
- URI-only content for large persisted outputs;
- imports from both `dbgpt.core.interface.report` and `dbgpt.core`;
- rejection of blank identifiers and content;
- duplicate artifact, section, and block IDs;
- dangling evidence and artifact input references;
- a chart block that references a non-chart artifact;
- direct and indirect artifact dependency cycles;
- a chart artifact without a SQL or Python dependency;
- missing artifact content;
- undeclared fields.

Implementation follows test-driven development: each behavior is introduced
by a focused failing test, followed by the smallest implementation that makes
it pass. Final verification runs the focused tests, the relevant Core test
suite, Ruff, and mypy against the new protocol module.

## Follow-up PR Boundaries

PR1 intentionally creates no runtime behavior. Expected later PRs are:

1. capture SQL, Python, chart, and reference artifacts from existing data
   assistant execution outputs;
2. persist reports and artifacts using DB-GPT's Serve/service/DAO patterns;
3. assemble report blocks from captured artifacts;
4. replay dependencies and create refreshed report versions;
5. expose APIs and render the schema in the existing data-assistant UI.

Keeping these integrations separate makes PR1 independently reviewable and
prevents a new core interface from being coupled to one current scene or UI.
