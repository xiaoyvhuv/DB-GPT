# Execution Artifact Capture Design

## Context

The report protocol can represent SQL, Python, chart, and reference artifacts,
and the report service can persist immutable report revisions. The running
system does not yet produce those artifacts from real executions.

The current DB-GPT paths expose two important boundaries:

- `BaseAgent.act()` already binds tool-result storage through a context-local
  interface for the duration of an action.
- Agentic SQL and Python tools still have the complete execution input and
  output before their user/LLM-facing result is formatted or truncated.

Capture therefore belongs at semantic execution boundaries and must not parse
SSE history, rendered Markdown, or model reasoning.

## Goals

- Assign stable artifact and execution identifiers during a conversation run.
- Capture status, duration, errors, content hashes, summaries, and dependencies.
- Work across regular agents, skills, tools, and future agent loops.
- Make capture opt-in and zero-impact when no collector is bound.
- Preserve typed artifacts until report assembly creates one immutable report
  revision.
- Never persist chain-of-thought, credentials, or connector secrets.

## Non-goals

- Report block assembly and narrative generation (PR5a).
- Report persistence on every tool call.
- User-visible version history or editing.
- Confidence scoring.
- A graph database or ReAct-specific event schema.

## Architecture

### Protocol versioning

Capture outcomes are an explicit schema-version-2 extension. Version 1 remains
readable and serializes with its original artifact field set; absent v2 fields
are omitted so a v1 payload is still accepted by the original v1 consumer.
Version 2 requires every artifact to carry `status` and `duration_ms`, permits a
failed SQL artifact without a fabricated result, and preserves Python stderr
and a missing process exit code for timeouts. A v1 document cannot be
mislabelled while carrying these fields.

### Core capture context

`dbgpt.core.interface.report_capture` provides:

- immutable `ArtifactCaptureContext` correlation metadata;
- an `ArtifactSink` protocol;
- a thread-safe `InMemoryArtifactSink` for one logical run;
- context-local bind/get helpers with correct nested restoration;
- canonical SHA-256 helpers for bytes, text, and JSON-compatible values.

The sink generates IDs and `ExecutionReference` values. Tool adapters create
typed protocol artifacts and record them through the active sink. If no sink is
bound, tool behavior remains byte-for-byte compatible.

The capture context uses a report/capture run identifier independent of the
event-stream step identifier. A tool invocation receives a unique generated
`step_id`; UI steps may later reference captured artifact IDs without rewriting
the immutable artifact identity.

### Runtime binding

The agentic request creates one in-memory sink and binds it around the agent
task. Context propagation covers regular tool calls without changing every tool
signature. Sub-agent adapters may bind a child context while sharing the same
run-level sink.

This is a cross-cutting execution extension, not logic embedded in the ReAct
decision loop.

### Semantic adapters

- SQL records normalized SQL, database identity, columns, row count, a bounded
  inline preview or content URI, and the hash of the complete result before
  Markdown truncation.
- Python records user code, actual exit code, full-output hash, bounded stdout,
  timeout/failure state, and generated image artifacts.
- Chart execution records its chart specification/output and depends on the
  SQL or Python artifact that produced the data.

Generic tool interception may attach correlation metadata but must not invent
typed SQL/Python/chart artifacts by parsing strings.

## Storage lifecycle

PR3 keeps artifacts in the run-level sink. PR5a assembles them into a
`ReportDocument`, and PR2 atomically stores the resulting immutable revision.
Small JSON previews may be inline; large payloads and images use an existing
DB-GPT file-storage URI, with only metadata and hashes stored in relational
tables.

Retention follows report ownership/deletion until the community defines a
separate retention policy. No hard-coded TTL is introduced.

## Compatibility and safety

- Existing tools behave identically when capture is not bound.
- Artifact snapshots are deep copies so later mutation cannot rewrite evidence.
- Duplicate artifact IDs and cross-conversation artifacts are rejected.
- Artifacts must belong to the sink's run and reference already-recorded inputs.
- Core identifier limits are shared with the relational persistence model.
- Hashing rejects non-finite JSON values and uses canonical UTF-8 JSON.
- Only execution summaries are captured; model thought text is excluded.
