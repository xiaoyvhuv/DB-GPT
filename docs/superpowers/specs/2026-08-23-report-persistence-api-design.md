# Report Persistence and API Design

## Scope

PR2 persists and reads the `ReportDocument` protocol introduced by PR1. It does
not execute SQL or Python, capture new artifacts, refresh reports, render the
report UI, or expose model reasoning.

The implementation follows the existing DB-GPT serve layout:

- a `dbgpt_serve.report` package owns config, lifecycle, API, service, and DAO;
- `BaseServe` mounts the router and registers SQLAlchemy metadata;
- `BaseDao.session()` provides the transaction boundary;
- endpoints resolve the authenticated owner from headers and fail closed until
  their service is bound;
- the application registers the serve in `serve_initialization.py`, and imports
  its entities in `db_model_initialization.py`.

## Persistence model

The relational database is the source of truth. No graph database is required.
Each save creates an immutable report run (the first form of a report revision)
inside one transaction.

### `dbgpt_report`

Stores stable report identity and owner scope: `report_id`, `owner_id`, title,
source conversation ID, latest run ID, latest revision, and timestamps. The
`(owner_id, report_id)` pair is unique.

### `dbgpt_report_run`

Stores one immutable snapshot: `run_id`, report identity, owner, monotonically
increasing `revision`, protocol `schema_version`, trigger type, optional parent
run, status, timestamps, and the canonical `ReportDocument` JSON. A run is the
persisted version unit; PR2 deliberately avoids a second `Version` table whose
meaning would duplicate it.

### `dbgpt_report_artifact`

Indexes each artifact within a run. It stores the artifact type, execution
coordinates, and canonical artifact JSON. Artifact content remains governed by
the PR1 protocol: small values may be inline; durable or large values use a
`FileStorageClient` URI. PR2 does not invent another blob store.

### `dbgpt_report_artifact_dependency`

Stores directed execution/product lineage (`input_artifact_id` ->
`artifact_id`) for query and replay planning. It is not a reasoning trace.

### `dbgpt_report_evidence`

Indexes block-to-artifact evidence links with role, source selector, and report
target selector. This makes evidence expansion queryable without parsing the
whole report snapshot.

Blocks themselves remain in `document_json`; a separate block table would only
duplicate ordered presentation state in PR2.

## Service invariants

- Every operation is owner-scoped; a report owned by someone else appears not
  found.
- Persistence rejects non-finite JSON, creates one canonical JSON snapshot, and
  revalidates that snapshot before deriving any normalized row. This also
  protects the boundary from a protocol object mutated after construction.
- Identifiers and titles are checked against the corresponding relational
  column lengths before a transaction begins.
- `report_id` and source `conv_uid` are stable across revisions.
- `run_id` is server-generated and immutable.
- A save atomically writes the run, all artifact/evidence/dependency indexes,
  and advances the report's latest pointer.
- Revisions increase monotonically per owner/report.
- Existing report rows are locked during revision allocation. First-write,
  revision-unique, deadlock, and serialization conflicts use a bounded retry;
  exhausted contention returns a stable conflict instead of leaking a database
  exception.
- Stored protocol JSON is canonical (`model_dump_json`) and is the lossless
  reconstruction source.

## API

Mounted at `/api/v1/reports` using the standard `Result` envelope.

- `POST /` saves a document and returns its run metadata plus document.
- `GET /{report_id}` returns the latest run.
- `GET /{report_id}/runs` lists immutable revision summaries newest first.
- `GET /{report_id}/runs/{run_id}` returns an exact snapshot.
- `GET /{report_id}/runs/{run_id}/blocks/{block_id}/evidence` returns evidence
  records together with their referenced artifacts.

The authenticated owner comes from DB-GPT's existing `get_user_from_headers`
dependency. Write requests never accept an owner field.

This follows the repository's trusted-header authentication perimeter: in a
production deployment, the upstream auth/proxy layer must authenticate and set
`user-id`; anonymous DB-GPT mode retains the existing shared owner `001`.

## Compatibility and migrations

Models use DB-GPT's portable SQLAlchemy conventions: SQLite-compatible integer
primary keys, indexed string identifiers, and MySQL `LONGTEXT` variants for
JSON snapshots. Entity imports participate in the existing startup metadata and
Alembic discovery path. Because DB-GPT does not auto-create MySQL tables at
startup, the canonical MySQL schema and current `v0_8_2` full/upgrade scripts
also create the five report tables. No existing table or API changes.

## Deferred work

PR3/PR4 create real artifacts and source evidence. PR5 assembles and renders
reports. PR6 adds refresh/replay orchestration and richer revision navigation.
Confidence scoring remains an interface-only future extension.
