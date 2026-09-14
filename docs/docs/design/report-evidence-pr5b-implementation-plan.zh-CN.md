# Report Evidence PR5b：确定性 Claim 验证实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Schema v3 报告实现有界证据解析、数值/趋势确定性验证和可解释聚合，不接入语义模型，也不改 Agent 主循环。

**Architecture:** `dbgpt-core` 保持跨层协议；`dbgpt-serve/report/verification` 提供无副作用的 packet builder、resolver、verifier、router 和 aggregator；ReportService 在保存前可选地运行这条 pipeline。DAO 继续保存不可变 ReportDocument JSON 和已有关系型索引。

**Tech Stack:** Python、DB-GPT Pydantic compatibility layer、pytest、ruff。

**Spec:** `docs/docs/design/report-evidence-comprehensive-architecture.zh-CN.md`

## Global Constraints

- 使用 `E:\software\Anaconda\envs\dbgpt_env`，不得使用 `.env`、`.venv` 或 `uv run`。
- 不修改 ReAct Agent 主循环、Tool 返回值或 Artifact Capture 的失败语义。
- 不引入 MiniCheck、AlignScore、LLM Judge、网络调用或图数据库。
- Schema v1/v2 必须继续可读；Schema v3 只能追加兼容字段。
- EvidenceHandle 必须属于当前 EvidencePacket；伪造 handle 必须解析失败。
- Claim/Verification 暂存 ReportDocument JSON；本 PR 不建 Claim 表。
- 每个任务先写失败测试、运行确认失败、最小实现、运行确认通过，再提交。

## 文件与职责

| 文件 | 职责 |
| --- | --- |
| `packages/dbgpt-core/src/dbgpt/core/interface/metric.py` | 指标语义、方向和容差的纯值对象 |
| `packages/dbgpt-core/src/dbgpt/core/interface/report.py` | 兼容扩展 VerificationResult |
| `packages/dbgpt-serve/src/dbgpt_serve/report/verification/evidence.py` | EvidencePacketBuilder 与显式 Resolver |
| `packages/dbgpt-serve/src/dbgpt_serve/report/verification/exact.py` | 数值、单位与容差验证 |
| `packages/dbgpt-serve/src/dbgpt_serve/report/verification/trend.py` | 趋势与业务阈值验证 |
| `packages/dbgpt-serve/src/dbgpt_serve/report/verification/router.py` | 按 Claim 类型选择 deterministic verifier |
| `packages/dbgpt-serve/src/dbgpt_serve/report/verification/aggregation.py` | WeightResolver、Coverage/FGS/BCGS 聚合 |
| `packages/dbgpt-serve/src/dbgpt_serve/report/service.py` | 可选 pipeline 集成和降级保存 |
| `packages/dbgpt-serve/src/dbgpt_serve/report/tests/test_verification.py` | 全部验证器单元测试 |

---

### Task 1: 扩展核心协议与 MetricDefinition

**Files:**

- Create: `packages/dbgpt-core/src/dbgpt/core/interface/metric.py`
- Modify: `packages/dbgpt-core/src/dbgpt/core/interface/report.py`
- Modify: `packages/dbgpt-core/src/dbgpt/core/__init__.py`
- Test: `packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report.py`

**Produces:** `MetricDefinition`、`BetterDirection`；扩展 `ClaimVerificationResult` 的 status/method/diagnostics 字段。

- [ ] 写失败测试：创建 `overdue_rate` 定义（ratio、LOWER、容差、显著变化 0.01），并断言下列结果合法：

```python
result = ClaimVerificationResult(
    claim_id="c-rate", status="insufficient_evidence",
    verification_method="business_rule", evidence_artifact_ids=[],
    verifier_name="trend-rule", verifier_version="1",
    reason_code="INSUFFICIENT_RULE", details={"metric_key": "overdue_rate"},
    latency_ms=7,
)
assert result.reason_code == "INSUFFICIENT_RULE"
```

- [ ] 运行并确认失败：

```powershell
& 'E:\software\Anaconda\envs\dbgpt_env\python.exe' -m pytest packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report.py -k 'metric_definition or machine_readable_failure' -q
```

- [ ] 最小实现：定义 `BetterDirection(HIGHER, LOWER, NEUTRAL)`；`MetricDefinition(metric_key, display_name, unit, aliases, better_direction, absolute_tolerance, relative_tolerance, significant_change)`；为 VerificationStatus 追加 `insufficient_evidence/error`，为 VerificationMethod 追加 `comparison_rule/trend_rule/business_rule`，追加可选 `reason_code/details/latency_ms`。不删除现有值。
- [ ] 运行 `test_report.py -q`，确认通过。
- [ ] 提交：`feat(report): extend deterministic verification protocol`。

### Task 2: 构建和解析有界 EvidenceHandle

**Files:**

- Create: `packages/dbgpt-serve/src/dbgpt_serve/report/verification/__init__.py`
- Create: `packages/dbgpt-serve/src/dbgpt_serve/report/verification/evidence.py`
- Test: `packages/dbgpt-serve/src/dbgpt_serve/report/tests/test_verification.py`

**Consumes:** 成功 SQL/Python Artifact 的内联 `columns/rows` 与 `Claim.normalized["evidence_handles"]`。  
**Produces:** `EvidencePacketBuilder.build(artifacts)`、`EvidenceResolver.resolve(claim, packet, artifacts)` 和 `ResolvedEvidence`。

- [ ] 写失败测试：SQL 结果 `[{"month":"2026-08","overdue_rate":0.0813}]` 产生一个 `sql_cell` handle，其 metadata 包含 `metric_key="overdue_rate"`；Claim 引用 `forged-handle` 时 resolver 返回空列表。
- [ ] 运行并确认失败：

```powershell
& 'E:\software\Anaconda\envs\dbgpt_env\python.exe' -m pytest packages/dbgpt-serve/src/dbgpt_serve/report/tests/test_verification.py -k 'packet_builder or forged' -q
```

- [ ] 最小实现：只为成功且内联表格的 SQL/Python Artifact 建立 handle；handle ID 由 `artifact_id,row,column` 的 SHA-256 前缀生成；Resolver 仅解析 packet 内显式 handle，返回含 artifact、selector、value、metadata 的冻结 `ResolvedEvidence`。V1 不做语义检索。
- [ ] 运行 `test_verification.py -q` 和 `ruff check packages/dbgpt-serve/src/dbgpt_serve/report/verification`。
- [ ] 提交：`feat(report): resolve bounded evidence handles`。

### Task 3: 实现 ExactMetricVerifier 与 TrendRuleVerifier

**Files:**

- Create: `packages/dbgpt-serve/src/dbgpt_serve/report/verification/exact.py`
- Create: `packages/dbgpt-serve/src/dbgpt_serve/report/verification/trend.py`
- Modify: `packages/dbgpt-serve/src/dbgpt_serve/report/verification/__init__.py`
- Test: `packages/dbgpt-serve/src/dbgpt_serve/report/tests/test_verification.py`

**Produces:** `ExactMetricVerifier.verify(claim, evidence, definition)` 和 `TrendRuleVerifier.verify(claim, evidence, definition)`。

- [ ] 写失败测试：`8.13%` 对 `0.0813 ratio` 为 `verified/exact_match`；同值但 `month=2026-07` 返回 `unsupported/TIME_SCOPE_MISMATCH`；趋势 Claim 要求 `significant` 而定义没有阈值时返回 `insufficient_evidence/INSUFFICIENT_RULE`。
- [ ] 运行并确认失败：

```powershell
& 'E:\software\Anaconda\envs\dbgpt_env\python.exe' -m pytest packages/dbgpt-serve/src/dbgpt_serve/report/tests/test_verification.py -k 'exact_metric or trend_rule' -q
```

- [ ] 最小实现：Exact 依次匹配 `metric_key`、time scope、dimensions、单位换算、绝对/相对容差；将 percent 统一换成 ratio。Trend 读取当前值、基准值、better direction、direction/magnitude；有显著阈值才判断 `significant`，没有则不得调用模型。所有结果写 `details` 和机器可读 `reason_code`。
- [ ] 运行 `test_verification.py` 与 core `test_report.py`，确认通过。
- [ ] 提交：`feat(report): verify metric and trend claims deterministically`。

### Task 4: 实现 Router、业务重要性与报告聚合

**Files:**

- Create: `packages/dbgpt-serve/src/dbgpt_serve/report/verification/router.py`
- Create: `packages/dbgpt-serve/src/dbgpt_serve/report/verification/aggregation.py`
- Test: `packages/dbgpt-serve/src/dbgpt_serve/report/tests/test_verification.py`

**Produces:** `VerifierRouter`、`RuleBasedWeightResolver`、`ReportGroundingSummary`、`ReportAggregator`。

- [ ] 写失败测试：metric Claim 经 router 返回 `exact_match`；未知 deterministic Claim 返回 `unverifiable/NO_DETERMINISTIC_VERIFIER`；两个 Critical Claim（一真一假）加一个 Recommendation 时，Coverage=1、FGS=0.5、BCGS=0.5，Recommendation 不进入分母。
- [ ] 运行并确认失败：

```powershell
& 'E:\software\Anaconda\envs\dbgpt_env\python.exe' -m pytest packages/dbgpt-serve/src/dbgpt_serve/report/tests/test_verification.py -k 'router or aggregator' -q
```

- [ ] 最小实现：Router 仅路由 `metric -> ExactMetricVerifier`、`trend -> TrendRuleVerifier`；其余明确返回不可验证。WeightResolver 将 `primary_metric_keys` 中的 metric 标为 Critical=3，其余 Supporting=1；Aggregator 排除 recommendation/`verifiable=false`，独立统计 contradicted，绝不生成单一“可信度”。
- [ ] 运行 `test_verification.py -q`，确认通过。
- [ ] 提交：`feat(report): aggregate deterministic claim grounding`。

### Task 5: 在 ReportService 中集成并保证兼容

**Files:**

- Modify: `packages/dbgpt-serve/src/dbgpt_serve/report/service.py`
- Modify: `packages/dbgpt-serve/src/dbgpt_serve/report/domain.py`
- Test: `packages/dbgpt-serve/src/dbgpt_serve/report/tests/test_service.py`

**Produces:** 可选 `verification_pipeline` 依赖；已验证的 Schema v3 报告快照与 `metadata["grounding"]`。

- [ ] 写失败测试：注入 deterministic pipeline 的 Schema v3 文档保存后，Claim 有 `verification.status="verified"` 且 metadata 含 `factual_grounding_score=1.0`；无 Claim 的 Schema v2 文档经默认 service 保存后不含 `grounding`，并保持 schema=2。
- [ ] 运行并确认失败：

```powershell
& 'E:\software\Anaconda\envs\dbgpt_env\python.exe' -m pytest packages/dbgpt-serve/src/dbgpt_serve/report/tests/test_service.py -k 'verified_schema_v3 or legacy_v2' -q
```

- [ ] 最小实现：service 仅对有 Claim 的 Schema v3 文档深拷贝并运行 pipeline；逐 Claim 写回结果，summary 写入 metadata。pipeline 异常只将受影响 Claim 标为 `error/VERIFICATION_PIPELINE_ERROR`，仍交由 DAO 保存；v1/v2 和无 Claim 文档不进入 pipeline。
- [ ] 运行完整回归：

```powershell
& 'E:\software\Anaconda\envs\dbgpt_env\python.exe' -m pytest packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report.py packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report_assembly.py packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report_capture.py packages/dbgpt-serve/src/dbgpt_serve/report/tests -q
& 'E:\software\Anaconda\envs\dbgpt_env\Scripts\ruff.exe' check packages/dbgpt-core/src/dbgpt/core/interface packages/dbgpt-serve/src/dbgpt_serve/report
```

- [ ] 提交：`feat(report): persist deterministic claim verification`。

## Self-Review

- 覆盖范围：协议、EvidencePacket、Resolver、Exact/Trend、Router、Weight/Aggregator、Service 持久化和兼容性均有独立任务；语义模型、前端和刷新明确不在 PR5b。
- 一致性：resolver 输出 `ResolvedEvidence`，verifier/router 输出 `ClaimVerificationResult`，aggregator 输出 `ReportGroundingSummary`，Service 持久化最终 `ReportDocument`。
- 可验收性：每个任务都包含失败测试、具体命令、最小行为与独立 commit。
