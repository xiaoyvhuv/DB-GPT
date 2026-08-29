# Report / Evidence PR5a Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不破坏 Schema v1/v2 报告的前提下，为 Schema v3 增加 Claim、Segment、VerificationResult 和 EvidencePacket 协议。

**Architecture:** `report.py` 继续拥有可持久化的 ReportDocument 协议；Claim/Segment/VerificationResult 属于 InsightBlock 和报告完整性校验。EvidencePacket 是独立的 core interface 值对象，不依赖 Agent、数据库或 LLM。Schema v3 才要求事实性 InsightBlock 具备 Claim 和 Segment。

**Tech Stack:** Python、Pydantic v2、pytest、DB-GPT core interfaces。

**Spec:** `docs/docs/design/report-evidence-architecture-review.zh-CN.md`

## Global Constraints

- 保持 `dbgpt-core` 不依赖 `dbgpt-app`、`dbgpt-serve` 或前端。
- Schema v1/v2 的现有 JSON 必须继续反序列化。
- 不实现 EvidencePacketBuilder、EvidenceResolver、确定性 Verifier 或语义 Verifier。
- 不改变既有 Artifact、Block 或 API 行为。
- 生产代码之前必须先写对应失败测试。

---

### Task 1: Schema v3 Claim、Segment 和验证结果协议

**Files:**
- Modify: `packages/dbgpt-core/src/dbgpt/core/interface/report.py`
- Modify: `packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report.py`

**Interfaces:**
- Produces: `Claim`, `TextSegment`, `ClaimSegment`, `ReportTextSegment`, `ClaimVerificationResult`。
- Produces: `InsightBlock.segments`、`InsightBlock.claims`、`Claim.verification`。
- Produces: `ReportDocument.schema_version: Literal[1, 2, 3]`。

- [x] **Step 1: 写失败测试，描述 Schema v3 可往返序列化带两个可点击 Claim 的 InsightBlock**

```python
document = ReportDocument.model_validate(_valid_v3_report_payload())
assert document.schema_version == 3
assert document.sections[0].blocks[0].claims[0].claim_id == "c-overdue"
assert document.model_validate_json(document.model_dump_json()) == document
```

- [x] **Step 2: 运行单测并确认因缺少 Schema v3/Claim/Segment 支持而失败**

Run: `E:\software\Anaconda\envs\dbgpt_env\python.exe -m pytest packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report.py -k schema_v3 -q`

Expected: FAIL，错误指向 `schema_version` 只允许 1/2 或 Claim/Segment 字段被拒绝。

- [x] **Step 3: 写失败测试，描述 v3 的完整性约束**

```python
@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda p: p["sections"][0]["blocks"][0].update({"segments": []}), "requires segments"),
        (lambda p: p["sections"][0]["blocks"][0].update({"claims": []}), "requires claims"),
        (lambda p: p["sections"][0]["blocks"][0]["segments"][1].update({"claim_id": "missing"}), "unknown claim"),
        (lambda p: p["sections"][0]["blocks"][0].update({"content": "changed"}), "segments must join"),
    ],
)
def test_schema_v3_rejects_invalid_claim_segment_integrity(mutate, message):
    payload = _valid_v3_report_payload()
    mutate(payload)
    with pytest.raises(ValidationError, match=message):
        ReportDocument.model_validate(payload)
```

- [x] **Step 4: 实现最小协议和验证**

在 `report.py` 中：

```python
class TextSegment(_ProtocolModel):
    type: Literal["text"] = "text"
    text: str

class ClaimSegment(_ProtocolModel):
    type: Literal["claim"] = "claim"
    segment_id: str
    claim_id: str
    text: str

class Claim(_ProtocolModel):
    claim_id: str
    claim_type: ClaimType
    text: str
    normalized: Dict[str, Any] = Field(default_factory=dict)
    verification: Optional[ClaimVerificationResult] = None
```

在 `ReportDocument` 根校验中，仅当 `schema_version == 3` 时校验 InsightBlock 的非空 Claim/Segment、文本拼接、唯一 ID、Segment 到 Claim 的引用以及 `target_selector.kind == "text_segment"` 的 block/segment/claim 一致性。

- [x] **Step 5: 运行聚焦测试并确认通过**

Run: `E:\software\Anaconda\envs\dbgpt_env\python.exe -m pytest packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report.py -q`

Expected: PASS。

### Task 2: EvidencePacket 值对象和公共导出

**Files:**
- Create: `packages/dbgpt-core/src/dbgpt/core/interface/evidence_packet.py`
- Modify: `packages/dbgpt-core/src/dbgpt/core/__init__.py`
- Modify: `packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report.py`

**Interfaces:**
- Produces: `EvidenceHandle` 和 `EvidencePacket`。
- Consumes: `artifact_id` 和 selector 字典；不解析 Artifact、不调用存储或模型。

- [x] **Step 1: 写失败测试，描述 EvidencePacket 的有界句柄和公共导出**

```python
packet = EvidencePacket(
    handles=[
        EvidenceHandle(
            handle_id="E3",
            artifact_id="sql-overdue",
            selector={"kind": "sql_cell", "row_key": {"month": "2026-08"}, "column": "overdue_rate"},
            value=0.0813,
        )
    ]
)
assert packet.handles[0].handle_id == "E3"
assert core.EvidencePacket is EvidencePacket
```

- [x] **Step 2: 运行该测试并确认因模块或导出不存在而失败**

Run: `E:\software\Anaconda\envs\dbgpt_env\python.exe -m pytest packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report.py -k evidence_packet -q`

Expected: FAIL，错误指向导入不存在。

- [x] **Step 3: 实现最小值对象和校验**

`EvidenceHandle` 校验非空 `handle_id`、`artifact_id` 和 `selector`，可选 `label`、`value`、`metadata`；`EvidencePacket` 校验 handle ID 唯一。二者使用 `extra="forbid"`，不增加 Builder 或数据库代码。

- [x] **Step 4: 运行 core 协议测试和 Ruff**

Run: `E:\software\Anaconda\envs\dbgpt_env\python.exe -m pytest packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report.py packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report_assembly.py -q`

Run: `E:\software\Anaconda\envs\dbgpt_env\Scripts\ruff.exe check packages/dbgpt-core/src/dbgpt/core/interface/report.py packages/dbgpt-core/src/dbgpt/core/interface/evidence_packet.py packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report.py`

Expected: PASS。

### Task 3: 回归验证与独立提交

**Files:**
- Modify: 本计划涉及的所有协议和测试文件。

- [x] **Step 1: 运行与 Report/Serve 兼容性相关的测试集**

Run: `E:\software\Anaconda\envs\dbgpt_env\python.exe -m pytest packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report.py packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report_assembly.py packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report_capture.py packages/dbgpt-serve/src/dbgpt_serve/report/tests -q`

Expected: PASS。

- [x] **Step 2: 执行格式与 diff 检查**

Run: `git diff --check`

Expected: 无空白错误。

- [x] **Step 3: 仅暂存 PR5a 相关文件并创建独立提交**

```bash
git add docs/docs/design/report-evidence-architecture-review.zh-CN.md \
        docs/docs/design/report-evidence-pr5a-implementation-plan.zh-CN.md \
        packages/dbgpt-core/src/dbgpt/core/interface/report.py \
        packages/dbgpt-core/src/dbgpt/core/interface/evidence_packet.py \
        packages/dbgpt-core/src/dbgpt/core/interface/__init__.py \
        packages/dbgpt-core/src/dbgpt/core/__init__.py \
        packages/dbgpt-core/src/dbgpt/core/interface/tests/test_report.py
git commit -m "feat(report): add claim evidence protocol"
```

Expected: 一个不包含既有工作区改动的 PR5a 提交。
