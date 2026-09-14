# DB-GPT 分析报告 Claim Verification Layer 设计稿

**状态：** 评审草案  
**适用范围：** 分析报告与证据图谱模块 PR5 及后续迭代  
**目的：** 为 9 月架构评审提供可讨论、可落地的结论验证方案。

## 1. 问题与目标

DB-GPT 已经能够在 Agent 执行过程中产生 SQL、Python、图表、知识库、Memory 与网页等 Artifact，并可将其组装为带 EvidenceReference 的 ReportDocument。现有“报告块关联证据”仍不足以回答一个关键问题：报告中的每一句结论是否真的由特定证据支持？

本设计新增 **Claim Verification Layer（结论验证层）**。它把自然语言报告拆成可定位的最小结论（Claim），优先用确定性规则复核，并只在确定性规则不能判断时使用语义验证模型。

本阶段的目标不是让另一个大模型笼统评价报告，而是提供：

- 可追溯：每条结论能回到具体 Artifact、行、列、片段或引用来源；
- 可复核：数值、计算、比较和趋势优先由可重复执行的规则判断；
- 可解释：前端能显示验证方式、规则、原始值和失败原因；
- 可聚合：报告同时给出证据覆盖度、事实支持度和业务关键结论支持度；
- 可演进：语义验证器和 LLM Judge 均为可插拔后端，不成为 V1 的前置依赖。

## 2. 设计原则与非目标

### 2.1 设计原则

1. **执行 provenance，不记录推理过程。** Evidence 记录实际执行输入、输出及依赖关系，不保存或暴露 Agent 的 chain-of-thought。
2. **确定性优先。** 能够由原始结果、重计算和业务规则判定的 Claim，不交给 LLM 判断。
3. **事实与重要性分离。** Verifier 只回答“证据是否支持 Claim”；WeightResolver 只回答“该 Claim 对当前问题有多重要”。
4. **引用优先、检索兜底。** Claim 显式引用的 EvidenceHandle 优先于结构化 lineage 匹配，二者仍不足时才走语义检索。
5. **协议兼容演进。** Schema v1/v2 继续可读；已落地的 Schema v3 Claim/Segment 协议不被破坏。强类型字段在验证层稳定后再通过后续 schema 版本固化。

### 2.2 非目标

- 不新建第二个 Agent loop，不修改 DB-GPT Agent/Tool 的正常执行方式；
- 不在 V1 引入图数据库；关系型持久化与 Artifact 依赖即可表示证据图；
- 不将 MiniCheck 或 LLM Judge 输出伪装成“数学精确可信度”；
- 不在 V1 自动定义“明显改善”等业务阈值；未定义规则应明确返回无法按规则验证。

## 3. 与当前实现的衔接

已实现的 Schema v3 已提供 `InsightBlock.content`、`segments`、`claims`、`ClaimVerificationResult` 与 `EvidencePacket`：

```text
Artifact -> EvidencePacket -> ReportAssembler -> ReportDocument
                                      |             |
                                      +--> Claim / Segment
```

它解决了“结论和可点击文本片段如何表示”的协议问题；尚未实现 EvidencePacketBuilder、EvidenceResolver、VerifierRouter、MetricDefinition、WeightResolver 和报告级聚合。

建议在现有模型上追加验证层，而不是重写 PR5a：

```text
SQL / Python / KB / Web / Chart
                |
             Artifact
                |
      EvidencePacketBuilder
                |
         ReportAssembler
                |
              Claim
                |
        EvidenceResolver
                |
         VerifierRouter
       /        |        \\
   Exact      Rule     Semantic
       \\        |        /
          VerificationResult
                |
          WeightResolver
                |
         ReportAggregator
                |
          ReportDocument
```

## 4. 核心领域协议

### 4.1 Claim

Claim 是一个最小、可定位的报告命题。一个段落中可包含多个 Claim，例如“8 月逾期率为 8.13%，整体履约情况明显改善”至少拆成数值 Claim 与趋势 Claim。

建议的目标字段如下。短期内结构化语义可保存在已存在的 `normalized` 中；当真实案例验证稳定后，再提升为 schema 的强类型字段。

| 字段 | 说明 |
| --- | --- |
| `claim_id` / `block_id` / `segment_id` | 全局标识、所在报告块与可点击文本位置 |
| `text` / `claim_type` | 原始文本和类型：metric、calculation、comparison、trend、ranking、descriptive、causal、recommendation |
| `verifiable` | 是否属于客观可验证事实；recommendation 默认不计入事实指标 |
| `metric_key`、`value`、`unit`、`operator` | 指标、数值、单位与关系表达式 |
| `time_scope`、`baseline_scope`、`dimensions` | 当前时间、比较基准与维度过滤条件 |
| `direction`、`magnitude` | 趋势方向和程度，如 improvement / significant |
| `evidence_handles` | 报告生成时显式引用的 EvidencePacket 句柄 |
| `parent_claim_id` / `child_claim_ids` | 复合结论的父子关系；父结论可由子结论聚合 |

例如“本月逾期率明显改善”应表达为趋势语义，而非只保留字符串：

```json
{
  "claim_type": "trend",
  "metric_key": "overdue_rate",
  "direction": "improvement",
  "magnitude": "significant",
  "time_scope": {"month": "2026-08"},
  "baseline_scope": {"month": "2026-07"}
}
```

### 4.2 MetricDefinition

MetricDefinition 是指标语义注册表，不是业务重要性表。它描述某个指标如何匹配、转换和解释：

| 字段 | 说明 |
| --- | --- |
| `metric_key`、`display_name`、`aliases` | 稳定标识、展示名和别名 |
| `unit`、`better_direction` | 单位以及越高越好、越低越好或中性 |
| `absolute_tolerance`、`relative_tolerance` | 展示四舍五入导致的可接受数值误差 |
| `comparison_operator` | 默认比较方式 |
| `trend_thresholds` | “显著改善/恶化”等明确业务阈值 |
| `freshness_seconds` | 证据新鲜度要求（可选） |

示例：`overdue_rate` 可声明单位为 ratio、展示为 percent、`better_direction=lower`，并规定“显著改善”为下降至少 1 个百分点。

### 4.3 EvidencePacket 与 EvidenceResolver

EvidencePacket 是从 Artifact 提取的、面向报告生成和验证的有界证据句柄，不是数据库实体，也不是模型可任意伪造的证据。EvidenceResolver 负责：

1. 校验 handle 是否属于当前报告的 EvidencePacket；
2. 还原其 `artifact_id` 和 selector；
3. 根据 Claim 的 `metric_key`、时间和维度补充候选证据；
4. 仅在以上两类都不足时调用 BM25/embedding 召回。

候选证据优先级：`显式 EvidenceHandle -> 结构化 lineage 匹配 -> 语义检索`。

### 4.4 VerificationResult

验证结果应保留“事实状态”和“验证方法”两个维度，避免把确定性等值判断与模型概率混为一个分数。

建议状态集合：

```text
verified | partially_supported | unsupported | insufficient_evidence |
contradicted | unverifiable | error
```

建议方法集合：

```text
exact_match | recompute | comparison_rule | trend_rule | business_rule
minicheck | alignscore | llm_judge
```

除现有 `claim_id`、`support_score`、证据引用和 verifier 信息外，增加：

- `reason_code`：如 `METRIC_MISMATCH`、`INSUFFICIENT_RULE`；
- `details`：规范化后的当前值、基准值、单位换算和匹配规则；
- `latency_ms`：便于观测模型验证成本。

`support_score` 仅用于语义验证模型的支持概率；UI 应显示为 *Semantic Support Score*，不应直接命名为“可信度”。

## 5. VerifierRouter：验证方法与顺序

路由器按 Claim 类型、结构化字段完整度及可用证据选择最低成本、最高可解释性的验证器：

| 优先级 | 验证器 | 适用 Claim | 结果依据 |
| --- | --- | --- | --- |
| P0 | ExactMetricVerifier | 数值指标 | 指标、时间、维度、单位、容差均匹配 |
| P1 | CalculationVerifier | 派生计算 | 基于原始 Artifact 重计算公式 |
| P2 | ComparisonVerifier | 同比、环比、差异 | 比较对象和算子满足 |
| P2 | TrendVerifier | 上升/下降 | `better_direction` 与变化方向一致 |
| P2 | BusinessRuleVerifier | “明显”“达标”等 | MetricDefinition 或正式知识规则已定义阈值 |
| P3 | SemanticGroundingVerifier | 描述性、综合解释 | 候选证据对 Claim 的语义支持 |
| P4 | LLMJudgeVerifier | 极少数复杂归因 | 明示为人工/模型辅助判断 |

### 5.1 数值验证的匹配条件

不能仅比较 `8.13 == 8.13`。ExactMetricVerifier 必须同时确认：

```text
metric_key 一致
AND time_scope 一致
AND dimensions 一致
AND unit 可转换
AND value 在容差范围内
```

因此，Evidence 的 `0.0813 ratio` 与报告的 `8.13%` 可以验证通过；同值但不同指标、不同月份则必须拒绝绑定。

### 5.2 趋势和阈值规则

“逾期率明显改善”需要：当前值、基准值、`lower_is_better` 与“明显”的正式阈值。若缺少阈值，返回：

```text
status = insufficient_evidence 或 unverifiable
reason_code = INSUFFICIENT_RULE
```

不允许 LLM 以常识自行决定下降多少才算“明显”。若知识库中存在经认可的业务规则，该规则本身可作为 Evidence。

### 5.3 复合 Claim

复合句必须拆成 atomic claims。父 Claim 的 `partially_supported` 来自可解释的子 Claim 聚合，而不是简单把语义分数 0.5-0.8 切成“部分支持”。

## 6. WeightResolver 与报告级指标

WeightResolver 不参与真假判断。它以用户问题、分析规范和报告上下文为输入，为每个 Claim 给出业务重要性：

| 等级 | 权重 | V1 规则 |
| --- | ---: | --- |
| Supporting | 1 | 辅助说明 |
| Important | 2 | 核心 KPI 的一级驱动 |
| Critical | 3 | 用户直接询问的 KPI、其比较结果或风险结论 |

例如“本月营收怎么样？”中的 GMV 与 GMV 环比应为 Critical；同一 GMV 在“配送效率为什么下降？”报告中可能只为 Supporting。故 ClaimType 不能代替 Importance。

报告聚合至少输出四项，而不是一个不可解释的总可信度：

```text
Evidence Coverage = 有至少一条候选证据的可验证 Claim / 全部可验证 Claim

Factual Grounding Score (FGS) = Σ claim_support / 可验证 Claim 数

Business-Critical Grounding Score (BCGS) = Σ(weight × claim_support) / Σ weight

Contradiction = contradicted 总数，以及其中 Critical 总数
```

其中 `verified=1`、`unsupported/contradicted=0`；父 Claim 可由子 Claim 的支持比例得到 0-1。推荐类与明确不可验证的主观表达不进入 FGS/BCGS。

## 7. 前端交互目标

Schema v3 的 `ClaimSegment` 为文本中嵌入数值提供稳定定位。前端点击不同片段，应展示不同验证卡片：

- 点击“8.13%”：指标、验证状态、Exact SQL Result、SQL Artifact、行列定位、原始值与展示值；
- 点击“明显改善”：当前/基准值、百分点变化、lower-is-better 规则、显著阈值；
- 点击综合性描述：Semantic Grounding、模型名称/版本、Semantic Support Score 与所用 Evidence。

这使同一句中不同数据与不同结论分别可审计，避免一个段落只绑定一个笼统的证据抽屉。

## 8. 分期建议与交付边界

### PR5-A：Claim 与确定性验证（建议下一步）

- 补齐 Claim 结构化语义的解析约定和 MetricDefinition；
- 实现 EvidenceResolver、VerifierRouter、Exact/Calculation/Comparison/Trend/BusinessRule Verifier；
- 实现规则型 WeightResolver 与 Coverage、FGS、BCGS 聚合；
- 用 Olist 案例验证数值、单位换算、月份错配、趋势阈值和矛盾案例。

### PR5-B：文本注解与前端验证体验

- 让实际 ReportAssembler 生成 Claim/Segment/EvidenceHandle；
- 在原生报告界面按 ClaimSegment 打开验证详情；
- 保持官方 PDF 导出能力复用，不新增 Markdown 导出。

### PR5-C：语义 Grounding 插件

- 引入 `SemanticGroundingVerifier` 插槽，V1 backend 选 MiniCheck；
- AlignScore 和 LLM Judge 仅保留接口/实验基线；
- 通过标注验证集校准 semantic threshold，记录模型与版本。

## 9. 测试与验收用例

| 场景 | 预期 |
| --- | --- |
| `8.13%` 对 `8.13%` | verified / exact_match |
| `0.0813 ratio` 对 `8.13%` | 单位换算后 verified |
| `4.1638213` 对 `4.16` | 在 MetricDefinition 容差内 verified |
| 相同数值但不同 metric_key | 不得误绑定 |
| 相同数值但不同月份/维度 | 不得误绑定 |
| 环比公式正确/错误 | verified / contradicted |
| lower-is-better 的下降/上升 | improvement / deterioration 规则结果正确 |
| “明显”有/无阈值 | business_rule / INSUFFICIENT_RULE |
| 父 Claim 两子项一真一假 | partially_supported 且可定位失败子项 |
| 描述性 Claim 支持、无关、矛盾 | semantic 结果可区分 |
| recommendation | 不计入 FGS/BCGS |

## 10. 评审需要确认的决策

1. **指标注册来源：** MetricDefinition 的初始来源是项目内 YAML/代码注册、知识库运营配置，还是数据库管理表？建议 V1 采用随服务发布的版本化 YAML，避免先引入后台配置系统。
2. **规则权威性：** “明显改善”“达标”等阈值由业务方提供、知识库正式文档提供，还是仅对明确注册的指标启用？建议仅接受前两者。
3. **Claim 生成责任：** V1 是否要求 LLM 产生结构化 Claim 与 EvidenceHandle，还是由报告组装器根据模板/数据自动生成一部分？建议核心 KPI 使用确定性模板，解释性结论允许 LLM 结构化输出。
4. **语义验证上线门槛：** MiniCheck 进入默认链路前，需使用 Olist 与人工标注中文/英文样本评测其 precision、召回、延迟和硬件成本。
5. **协议升级时机：** 建议先在 PR5-A 使用 `normalized` 验证字段并积累真实样本，确认字段稳定后再发布 Schema v4 强类型 Claim，避免过早固化公共 wire contract。

## 11. 汇报时的核心表述

本模块不是“让另一个大模型判断第一个大模型说得对不对”。它采用 **确定性优先、语义验证兜底** 的分层机制：数值事实回到原始结果，计算事实重新计算，趋势和业务语义依据指标定义与正式规则，解释性结论才进入 Claim-level semantic grounding，复杂归因才在必要时由 LLM Judge 辅助。

同时，事实支持度与业务重要性严格分离：Verifier 回答“证据是否支持 Claim”，WeightResolver 回答“该 Claim 对当前业务问题是否重要”。最终呈现 Coverage、FGS、BCGS 与矛盾计数，而不是一个缺少解释依据的“AI 可信度百分比”。

## 12. 参考方向

- FActScore：长文本拆分为 atomic facts 后逐事实核验的思想来源；
- MiniCheck：生成 Claim 与 grounding document 的支持关系检查，作为 PR5-C 的候选后端；
- AlignScore：通用文本 alignment，可作为后续语义验证的对照基线；
- DB-GPT Agent/Tool/Knowledge 架构：本模块位于真实执行之后的 provenance 与 verification 层，不另造 Agent 执行循环。
