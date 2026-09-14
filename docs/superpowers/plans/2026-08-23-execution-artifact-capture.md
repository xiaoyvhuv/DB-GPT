# Execution Artifact Capture Implementation Plan

## PR3a: Core capture infrastructure

1. Add schema version 2 for execution status, duration, error, Python stderr,
   and failed SQL executions without fabricated results, while preserving the
   exact version-1 serialized field set.
2. Add capture-context tests for ID/reference generation, nested restoration,
   task isolation, duplicate/cross-conversation rejection, immutable snapshots,
   and canonical hashing.
3. Implement `ArtifactCaptureContext`, `ArtifactSink`,
   `InMemoryArtifactSink`, binding helpers, shared persistence limits, strict
   run/dependency validation, and hashing helpers in `dbgpt-core`.
4. Export the public capture API and run Core/report regression tests, Ruff,
   formatting, and mypy.

## PR3b: Real execution adapters

1. Bind one capture sink around an agentic request without adding branches to
   the ReAct decision loop.
2. Add failing tests for successful, empty, failed, and truncated SQL results;
   capture the complete-result hash before display truncation.
3. Add failing tests for successful, failed, timed-out, and image-producing
   Python executions; capture actual subprocess results before display
   truncation.
4. Add failing tests for chart creation and SQL/Python-to-chart dependencies in
   both the native `ChartAction` path and agentic image path.
5. Remove or delegate duplicated sub-agent SQL/Python implementations so they
   use the same semantic adapters.
6. Run focused, adjacent agent/tool, report, lint, type, and compatibility
   verification before committing the PR.
