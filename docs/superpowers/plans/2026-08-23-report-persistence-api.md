# Report Persistence and API Implementation Plan

> Execute this plan with the repository's existing Conda environment
> `dbgpt_env`; never create or use `.venv`.

## Task 1: Define persistence entities

**Files:**

- Create: `packages/dbgpt-serve/src/dbgpt_serve/report/models/models.py`
- Create: `packages/dbgpt-serve/src/dbgpt_serve/report/models/__init__.py`
- Test: `packages/dbgpt-serve/src/dbgpt_serve/report/tests/test_models.py`
- Modify: `assets/schema/dbgpt.sql`
- Modify: `assets/schema/upgrade/v0_8_2/v0.8.2.sql`
- Modify: `assets/schema/upgrade/v0_8_2/upgrade_to_v0.8.2.sql`
- Test: `packages/dbgpt-serve/src/dbgpt_serve/report/tests/test_schema_files.py`

Write failing model tests for table names, constraints, portable primary keys,
and metadata creation. Add Report, ReportRun, ReportArtifact,
ReportArtifactDependency, and ReportEvidence entities with the indexes described
in the design. Run the focused test and format/lint the files.
Mirror those tables in DB-GPT's canonical MySQL bootstrap and current upgrade
scripts, since MySQL startup deliberately does not run ORM `create_all()`.

## Task 2: Implement atomic owner-scoped DAO

**Files:**

- Create: `packages/dbgpt-serve/src/dbgpt_serve/report/models/dao.py`
- Create: `packages/dbgpt-serve/src/dbgpt_serve/report/domain.py`
- Test: `packages/dbgpt-serve/src/dbgpt_serve/report/tests/test_dao.py`

Write failing tests for first save, revision increments, latest/exact reads,
owner isolation, list order, normalized artifact dependencies, evidence lookup,
rollback, and JSON validation. Implement one DAO transaction that persists the
complete snapshot plus relational indexes. Cover mutation revalidation,
non-finite JSON, MySQL-aligned length bounds, and concurrent first/append saves
with bounded conflict retries. Expose immutable domain records, not ORM entities.

## Task 3: Add service and API schemas

**Files:**

- Create: `packages/dbgpt-serve/src/dbgpt_serve/report/service.py`
- Create: `packages/dbgpt-serve/src/dbgpt_serve/report/api/schemas.py`
- Create: `packages/dbgpt-serve/src/dbgpt_serve/report/api/__init__.py`
- Test: `packages/dbgpt-serve/src/dbgpt_serve/report/tests/test_service.py`

Write failing tests for request-to-document validation, immutable run metadata,
not-found behavior, and evidence response mapping. Implement the thin service
over the DAO and strict Pydantic wire schemas.

## Task 4: Add owner-aware endpoints

**Files:**

- Create: `packages/dbgpt-serve/src/dbgpt_serve/report/api/endpoints.py`
- Test: `packages/dbgpt-serve/src/dbgpt_serve/report/tests/test_endpoints.py`

Write failing FastAPI tests for all five routes, owner isolation, malformed
documents, and the unbound-service failure. Implement endpoints with `Result`,
`get_user_from_headers`, `run_in_threadpool`, stable error codes, and no owner
field in request bodies.

## Task 5: Register the serve lifecycle

**Files:**

- Create: `packages/dbgpt-serve/src/dbgpt_serve/report/config.py`
- Create: `packages/dbgpt-serve/src/dbgpt_serve/report/serve.py`
- Create: `packages/dbgpt-serve/src/dbgpt_serve/report/__init__.py`
- Modify: `packages/dbgpt-app/src/dbgpt_app/initialization/serve_initialization.py`
- Modify: `packages/dbgpt-app/src/dbgpt_app/initialization/db_model_initialization.py`
- Test: `packages/dbgpt-serve/src/dbgpt_serve/report/tests/test_serve.py`

Write failing lifecycle tests, then mount `/api/v1/reports`, bind/unbind the
service, register entity metadata, and add the application registration/imports.

## Task 6: Verify PR2

Run the report test package, affected app initialization tests, Ruff check and
format check, mypy on the new package, `git diff --check`, and the broader
`dbgpt_serve` tests that are practical in the configured environment. Review the
diff against the design before committing PR2 as one functional change.
